import csv
import os
import time

import requests


HUBSPOT_API_BASE = "https://api.hubapi.com"
MAILCHIMP_API_BASE = "https://{dc}.api.mailchimp.com/3.0"
OUTPUT_CSV = os.environ.get(
    "CONTACT_COMPARISON_CSV", "hubspot_mailchimp_comparison.csv"
)


def get_hubspot_marketing_contacts(token):
    """Return HubSpot contacts whose marketable status is true."""
    contacts = []
    after = None
    page = 1
    headers = {"Authorization": f"Bearer {token}"}

    while True:
        params = {
            "limit": 100,
            "properties": "firstname,lastname,email,hs_marketable_status",
        }
        if after:
            params["after"] = after

        for attempt in range(4):
            try:
                response = requests.get(
                    f"{HUBSPOT_API_BASE}/crm/v3/objects/contacts",
                    headers=headers,
                    params=params,
                    timeout=30,
                )
            except requests.RequestException:
                if attempt == 3:
                    raise
                delay = 2**attempt
                print(
                    f"HubSpot connection failed; "
                    f"retrying page {page} in {delay} seconds...",
                    flush=True,
                )
                time.sleep(delay)
                continue

            if response.status_code not in {429, 500, 502, 503, 504}:
                break
            if attempt < 3:
                delay = 2**attempt
                print(
                    f"HubSpot returned {response.status_code}; "
                    f"retrying page {page} in {delay} seconds...",
                    flush=True,
                )
                time.sleep(delay)
        response.raise_for_status()
        data = response.json()

        for contact in data.get("results", []):
            properties = contact.get("properties", {})
            email = properties.get("email")
            status = properties.get("hs_marketable_status")
            if email and str(status).lower() == "true":
                contacts.append(
                    {
                        "first_name": properties.get("firstname") or "",
                        "last_name": properties.get("lastname") or "",
                        "email": email.strip().lower(),
                    }
                )

        print(
            f"HubSpot: processed page {page} "
            f"({len(contacts)} marketable contacts found)",
            flush=True,
        )
        page += 1

        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break

    return contacts


def get_mailchimp_subscribers(api_key, server_prefix, audience_id):
    """Return subscribed email addresses for a Mailchimp audience."""
    base_url = MAILCHIMP_API_BASE.format(dc=server_prefix)
    subscribers = set()
    offset = 0
    page = 1
    headers = {"Authorization": f"apikey {api_key}"}

    while True:
        for attempt in range(4):
            try:
                response = requests.get(
                    f"{base_url}/lists/{audience_id}/members",
                    headers=headers,
                    params={
                        "status": "subscribed",
                        "count": 1000,
                        "offset": offset,
                    },
                    timeout=30,
                )
            except requests.RequestException:
                if attempt == 3:
                    raise
                delay = 2**attempt
                print(
                    f"Mailchimp request timed out; "
                    f"retrying page {page} in {delay} seconds...",
                    flush=True,
                )
                time.sleep(delay)
                continue

            if response.status_code not in {429, 500, 502, 503, 504}:
                break
            if attempt < 3:
                delay = 2**attempt
                print(
                    f"Mailchimp returned {response.status_code}; "
                    f"retrying page {page} in {delay} seconds...",
                    flush=True,
                )
                time.sleep(delay)
        response.raise_for_status()
        data = response.json()
        members = data.get("members", [])

        subscribers.update(
            member["email_address"].strip().lower()
            for member in members
            if member.get("email_address")
            and member.get("status") == "subscribed"
        )
        offset += len(members)
        print(
            f"Mailchimp: processed page {page} "
            f"({len(subscribers)} subscribers found)",
            flush=True,
        )
        page += 1
        if offset >= data.get("total_items", 0) or not members:
            break

    return subscribers


def get_mailchimp_server_prefix(api_key):
    """Extract the Mailchimp data-center suffix from an API key."""
    if "-" not in api_key:
        raise ValueError("MAILCHIMP_API_KEY must include its server suffix, such as -us21.")
    return api_key.rsplit("-", 1)[1]


def write_comparison_csv(contacts, subscribed_emails, output_path):
    """Write HubSpot contacts with their Mailchimp subscription status."""
    with open(output_path, "w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["First Name", "Last Name", "Email", "Mailchimp Subscribed"])
        for contact in contacts:
            writer.writerow(
                [
                    contact["first_name"],
                    contact["last_name"],
                    contact["email"],
                    "Yes" if contact["email"] in subscribed_emails else "No",
                ]
            )


def main():
    required_environment = (
        "HUBSPOT_SERVICE_KEY",
        "MAILCHIMP_API_KEY",
        "MAILCHIMP_AUDIENCE_ID",
    )
    missing_environment = [
        name for name in required_environment if not os.environ.get(name)
    ]
    if missing_environment:
        raise SystemExit(
            "Set these environment variables before running: "
            + ", ".join(missing_environment)
        )

    hubspot_token = os.environ["HUBSPOT_SERVICE_KEY"]
    mailchimp_api_key = os.environ["MAILCHIMP_API_KEY"]
    mailchimp_server = get_mailchimp_server_prefix(mailchimp_api_key)
    mailchimp_audience_id = os.environ["MAILCHIMP_AUDIENCE_ID"]

    hubspot_marketing_contacts = get_hubspot_marketing_contacts(hubspot_token)
    mailchimp_subscribers = get_mailchimp_subscribers(
        mailchimp_api_key, mailchimp_server, mailchimp_audience_id
    )

    write_comparison_csv(
        hubspot_marketing_contacts,
        mailchimp_subscribers,
        OUTPUT_CSV,
    )
    overlapping_contacts = sum(
        contact["email"] in mailchimp_subscribers
        for contact in hubspot_marketing_contacts
    )
    print(f"Wrote {len(hubspot_marketing_contacts)} contacts to {OUTPUT_CSV}")
    print(f"Overlapping contacts: {overlapping_contacts}")


if __name__ == "__main__":
    main()