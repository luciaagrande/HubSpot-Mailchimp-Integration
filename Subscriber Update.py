"""CRM sync: set HubSpot's Mailchimp Subscriber property from Mailchimp.

Install requests, then set HUBSPOT_SERVICE_KEY, MAILCHIMP_API_KEY, and
MAILCHIMP_AUDIENCE_ID as environment variables. Set HUBSPOT_PROPERTY to the property's internal name
if it differs from 'mailchimp_subscriber'. Run with --apply to write;
without it the script only reports the changes it would make.
"""

import argparse
import os
import sys
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

HUBSPOT = "https://api.hubapi.com"


def create_retry_session():
	"""Create an HTTP session that retries transient API/network failures."""
	session = requests.Session()
	retry_policy = Retry(
		total=4,
		connect=4,
		read=4,
		status=4,
		backoff_factor=1,
		status_forcelist=(429, 500, 502, 503, 504),
		allowed_methods=frozenset({"GET", "PATCH"}),
		raise_on_status=False,
	)
	session.mount("https://", HTTPAdapter(max_retries=retry_policy))
	return session

def get_active_emails(api_key, audience_id, limit=0):
	dc = api_key.rsplit("-", 1)[-1]
	if dc == api_key:
		raise ValueError("MAILCHIMP_API_KEY must include a data-center suffix, such as -us1")
	session = create_retry_session()
	session.auth = ("user", api_key)
	base = f"https://{dc}.api.mailchimp.com/3.0/lists/{audience_id}/members"
	emails, offset = set(), 0
	page_number = 1
	while True:
		count = 250 if not limit else min(250, limit - len(emails))
		response = session.get(
			base,
			params={
				"status": "subscribed",
				"count": count,
				"offset": offset,
				"fields": "members.email_address,total_items",
			},
			timeout=60,
		)
		response.raise_for_status()
		data = response.json()
		members = data.get("members", [])
		emails.update(m["email_address"].strip().lower() for m in members if m.get("email_address"))
		offset += len(members)
        #Update user on progress, check on end
		print(f"Mailchimp: processed page {page_number} ({len(emails)} subscribed emails)", flush=True)
		page_number += 1
		if not members or offset >= data.get("total_items", 0) or limit and len(emails) >= limit:
			return emails
		time.sleep(0.2)

#Get hubspot contacts
def hubspot_contacts(token, limit=0, property_name="mailchimp_subscriber", session=None):
	url = f"{HUBSPOT}/crm/v3/objects/contacts"
	params = {
		"limit": 100 if not limit else min(100, limit),
		"properties": f"email,{property_name}",
	}
	headers = {"Authorization": f"Bearer {token}"}
	processed = 0
	session = session or create_retry_session()
	while url:
		response = session.get(url, headers=headers, params=params, timeout=(10, 60))
		response.raise_for_status()
		data = response.json()
		for contact in data.get("results", []):
			yield contact
			processed += 1
			if limit and processed >= limit:
				return
		url = data.get("paging", {}).get("next", {}).get("link")
		params = None

#Main function to sync the Mailchimp subscriber status to HubSpot contacts. 
def sync(apply=False, limit=1):
	token = os.environ.get("HUBSPOT_SERVICE_KEY")
	api_key = os.environ.get("MAILCHIMP_API_KEY")
	audience_id = os.environ.get("MAILCHIMP_AUDIENCE_ID")
	property_name = os.environ.get("HUBSPOT_PROPERTY", "mailchimp_subscriber")
	if not all((token, api_key, audience_id)):
		raise ValueError("Set HUBSPOT_SERVICE_KEY, MAILCHIMP_API_KEY, and MAILCHIMP_AUDIENCE_ID")
    
	active = get_active_emails(api_key, audience_id)
	session = create_retry_session()
	headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
	changed = unchanged = missing_email = 0
	processed = 0
	def print_progress():
		verb = "updated" if apply else "would update"
		print(
			f"Progress: processed {processed}; {verb} {changed}; "
			f"already correct {unchanged}; missing email {missing_email}",
			flush=True,
		)

	for contact in hubspot_contacts(token, limit, property_name, session):
		if limit and processed >= limit:
			break
		processed += 1
		properties = contact.get("properties", {})
		email = (properties.get("email") or "").strip().lower()
		if not email:
			missing_email += 1
			print_progress()
			continue
		desired = "true" if email in active else "false"
		if (properties.get(property_name) or "").lower() == desired:
			unchanged += 1
			print_progress()
			continue
		if apply:
			response = session.patch(
				f"{HUBSPOT}/crm/v3/objects/contacts/{contact['id']}",
				headers=headers,
				json={"properties": {property_name: desired}},
				timeout=(10, 60),
			)
			if not response.ok:
				raise requests.HTTPError(
					f"HubSpot update failed ({response.status_code}): {response.text}",
					response=response,
				)
			time.sleep(0.1)
		changed += 1
		print(f"{'Updated' if apply else 'Would update'} {email}", flush=True)
		print_progress()
	print(f"{'Updated' if apply else 'Would update'} {changed}; already correct {unchanged}; missing email {missing_email}.")


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--apply", action="store_true", help="perform updates (default: dry run)")
	parser.add_argument(
		"--limit",
		type=int,
		default=1,
		help="contacts to process; defaults to 1, use 0 for all contacts",
	)
	try:
		args = parser.parse_args()
		if args.limit < 0:
			raise ValueError("--limit must be zero or a positive number")
		sync(apply=args.apply, limit=args.limit)
	except (ValueError, requests.RequestException) as exc:
		print(f"Sync failed: {exc}", file=sys.stderr)
		sys.exit(1)
