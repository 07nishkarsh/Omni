"""
Script to push Policy Book markdown content directly to the Notion Page.
Uses the Notion REST API to create blocks matching the markdown content.
"""

import os
import sys
import httpx
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
POLICY_BOOK_ID = os.getenv("NOTION_POLICY_BOOK_ID")

if not NOTION_TOKEN or not POLICY_BOOK_ID:
    print("Error: Missing NOTION_TOKEN or NOTION_POLICY_BOOK_ID in .env")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}
BASE_URL = "https://api.notion.com/v1"

blocks = [
    {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Section I: Standard Loan Routing"}}]}
    },
    {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": "Clause 1: Standard loan routing follows a strict serial flow (A -> B -> C). Subsidy loans carry additional compliance rules. A verified guarantor can offset a primary risk flag. Disbursement-splitting rules must be evaluated prior to final approval."}}]}
    },
    {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": "Clause 2: Under emergency or disaster bypass conditions, routing flows directly from Agent A to Agent C for expedited execution, while Agent B monitors asynchronously."}}]}
    },
    {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": "Clause 3: Autonomous execution is strictly limited to an explicit threshold of ₹50,000."}}]}
    },
    {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": "Clause 4: ANY transaction touching a frozen or locked fund requires immediate human review, regardless of the transaction amount."}}]}
    },
    {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Section II: Negotiation Constraints"}}]}
    },
    {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": "Clause 1: Automated negotiations are capped at a maximum of three (3) rounds."}}]}
    },
    {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": "Clause 2: A \"structural change\" (e.g., changes to term length, collateral type, or interest rate tier) forces human review, even if the proposed amount is under the autonomous threshold."}}]}
    },
    {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Section III: Audit Requirements"}}]}
    },
    {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": "Clause 1: Every transaction must be logged with the following fields: TransactionID, AgentsInvolved, Route, Outcome, Timestamp, and PolicyVersion."}}]}
    }
]

def clear_existing_blocks():
    print("Fetching existing blocks to clear the page...")
    res = httpx.get(f"{BASE_URL}/blocks/{POLICY_BOOK_ID}/children", headers=HEADERS)
    res.raise_for_status()
    children = res.json().get("results", [])
    for block in children:
        httpx.delete(f"{BASE_URL}/blocks/{block['id']}", headers=HEADERS)

def append_new_blocks():
    print("Appending new structured blocks to the Policy Book...")
    payload = {"children": blocks}
    res = httpx.patch(f"{BASE_URL}/blocks/{POLICY_BOOK_ID}/children", json=payload, headers=HEADERS)
    if res.status_code != 200:
        print(f"Failed to append blocks: {res.text}")
    res.raise_for_status()

if __name__ == "__main__":
    clear_existing_blocks()
    append_new_blocks()
    print("✅ Successfully published the Policy Book to Notion!")
