"""
Setup script to initialize Notion databases and pages for the Bank Agent Orchestrator.

Requires NOTION_TOKEN and NOTION_PARENT_PAGE_ID to be set in the environment or .env file.
This script is idempotent; it checks for existing databases/pages by name before creating them.
"""

import os
import sys
import httpx
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_PARENT_PAGE_ID = os.getenv("NOTION_PARENT_PAGE_ID")

if not NOTION_TOKEN or NOTION_TOKEN.startswith("secret_xxxx") or NOTION_TOKEN == "mock-notion-token":
    print("Error: Please set a valid NOTION_TOKEN in your .env file.")
    sys.exit(1)

if not NOTION_PARENT_PAGE_ID:
    print("Error: Please set a NOTION_PARENT_PAGE_ID in your .env file (the page where these databases will be created).")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}

BASE_URL = "https://api.notion.com/v1"

def search_notion(query: str, filter_type: str = None) -> dict | None:
    """Search for a page or database by exact title."""
    payload = {"query": query}
    if filter_type:
        payload["filter"] = {"value": filter_type, "property": "object"}
    
    response = httpx.post(f"{BASE_URL}/search", json=payload, headers=HEADERS)
    response.raise_for_status()
    results = response.json().get("results", [])
    
    for res in results:
        title_prop = res.get("title") or res.get("properties", {}).get("title", {})
        if isinstance(title_prop, list) and title_prop and title_prop[0].get("plain_text") == query:
            return res
        if isinstance(title_prop, dict) and title_prop.get("title", []):
            if title_prop["title"][0].get("plain_text") == query:
                return res
    return None

def create_page(title: str) -> dict:
    existing = search_notion(title, "page")
    if existing:
        print(f"[SKIP] Page '{title}' already exists. ID: {existing['id']}")
        return existing

    payload = {
        "parent": {"type": "page_id", "page_id": NOTION_PARENT_PAGE_ID},
        "properties": {
            "title": [{"type": "text", "text": {"content": title}}]
        }
    }
    response = httpx.post(f"{BASE_URL}/pages", json=payload, headers=HEADERS)
    response.raise_for_status()
    data = response.json()
    print(f"[CREATED] Page '{title}'. ID: {data['id']}")
    return data

def create_database(title: str, properties: dict) -> dict:
    existing = search_notion(title, "database")
    if existing:
        print(f"[SKIP] Database '{title}' already exists. ID: {existing['id']}")
        return existing

    payload = {
        "parent": {"type": "page_id", "page_id": NOTION_PARENT_PAGE_ID},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": properties
    }
    response = httpx.post(f"{BASE_URL}/databases", json=payload, headers=HEADERS)
    response.raise_for_status()
    data = response.json()
    print(f"[CREATED] Database '{title}'. ID: {data['id']}")
    return data

def main():
    print("Initializing Notion workspace components...\n")

    # 1. Policy Book (Page)
    policy_page = create_page("Policy Book")

    # 2. Manager Approval Desk (Database)
    # Views cannot be configured via API, must be set in UI to Board View
    desk_props = {
        "TransactionID": {"title": {}},
        "Type": {"select": {}},
        "Amount": {"number": {"format": "number"}},
        "ProposedChange": {"rich_text": {}},
        "Status": {
            "select": {
                "options": [
                    {"name": "Awaiting Action", "color": "yellow"},
                    {"name": "Approved", "color": "green"},
                    {"name": "Rejected", "color": "red"}
                ]
            }
        },
        "SubmittedAt": {"date": {}}
    }
    desk_db = create_database("Manager Approval Desk", desk_props)

    # 3. Treasury & Scheme Ledger (Database)
    # Note: RunningBalance as a true running total is complex in Notion without external rollups or 
    # self-referential relations. We configure a basic formula here as placeholder.
    ledger_props = {
        "TransactionID": {"title": {}},
        "Fund": {"select": {}},
        "Delta": {"number": {"format": "number_with_commas"}},
        "RunningBalance": {"formula": {"expression": "prop(\"Delta\")"}},
        "Timestamp": {"date": {}}
    }
    ledger_db = create_database("Treasury & Scheme Ledger", ledger_props)

    # 4. Audit & Activity Feed (Database)
    audit_props = {
        "TransactionID": {"title": {}},
        "Summary": {"rich_text": {}},
        "AgentsInvolved": {"multi_select": {}},
        "Route": {"select": {}},
        "Outcome": {"select": {}},
        "Timestamp": {"date": {}},
        "PolicyVersion": {"rich_text": {}}
    }
    audit_db = create_database("Audit & Activity Feed", audit_props)

    print("\n=============================================")
    print("Setup Complete! Please save these IDs to your .env file:")
    print("=============================================")
    print(f"NOTION_POLICY_BOOK_ID={policy_page['id']}")
    print(f"NOTION_APPROVAL_DESK_ID={desk_db['id']}")
    print(f"NOTION_TREASURY_LEDGER_ID={ledger_db['id']}")
    print(f"NOTION_AUDIT_FEED_ID={audit_db['id']}")
    print("\nNOTE: Please open Notion and manually configure the following views:")
    print(" - 'Manager Approval Desk' -> Set view to Board")
    print(" - 'Treasury & Scheme Ledger' -> Configure RunningBalance rollup/relation if true running sum is needed")
    print(" - 'Audit & Activity Feed' -> Set view to List")

if __name__ == "__main__":
    main()
