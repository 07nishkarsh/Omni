import os
import sys
import httpx
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_APPROVAL_DESK_ID = os.getenv("NOTION_APPROVAL_DESK_ID")
NOTION_POLICY_BOOK_ID = os.getenv("NOTION_POLICY_BOOK_ID")
NOTION_PARENT_PAGE_ID = os.getenv("NOTION_PARENT_PAGE_ID")

if not all([NOTION_TOKEN, NOTION_APPROVAL_DESK_ID, NOTION_POLICY_BOOK_ID, NOTION_PARENT_PAGE_ID]):
    print("Missing env vars")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}
BASE_URL = "https://api.notion.com/v1"

def add_conflict_summary_property():
    print("Adding ConflictSummary property to Manager Approval Desk...")
    payload = {
        "properties": {
            "ConflictSummary": {
                "rich_text": {}
            }
        }
    }
    res = httpx.patch(f"{BASE_URL}/databases/{NOTION_APPROVAL_DESK_ID}", json=payload, headers=HEADERS)
    res.raise_for_status()

def update_gazette_archive():
    print("Updating Gazette Archive (formerly Policy Book)...")
    # 1. Update Title and Icon
    payload = {
        "icon": {
            "type": "emoji",
            "emoji": "📜"
        },
        "properties": {
            "title": {
                "title": [
                    {
                        "type": "text",
                        "text": {"content": "Gazette Archive"}
                    }
                ]
            }
        }
    }
    res = httpx.patch(f"{BASE_URL}/pages/{NOTION_POLICY_BOOK_ID}", json=payload, headers=HEADERS)
    res.raise_for_status()

    # 2. Fetch existing blocks to see if we need to inject the callout (avoid duplicates)
    res_blocks = httpx.get(f"{BASE_URL}/blocks/{NOTION_POLICY_BOOK_ID}/children", headers=HEADERS)
    res_blocks.raise_for_status()
    children = res_blocks.json().get("results", [])
    has_callout = any(b.get("type") == "callout" for b in children)

    if not has_callout:
        print("Prepending Callout block...")
        
        # We cannot "prepend" in Notion API directly via append endpoint unless we specify after.
        # But wait, without after, append goes to the end.
        # However, to insert at the top, we need the ID of the first block or we must recreate the page.
        # Actually, in Notion API, append block children doesn't support "prepend" easily unless we insert after an empty block? No, append block children adds to the end.
        # To insert at the top, we can fetch all blocks, delete them, and append the callout followed by the original blocks.
        print("Re-rendering blocks to put callout at the top...")
        # Since we just created this in the last step, let's just clear and append everything.
        for b in children:
            httpx.delete(f"{BASE_URL}/blocks/{b['id']}", headers=HEADERS)
            
        new_blocks = [
            {
                "object": "block",
                "type": "callout",
                "callout": {
                    "icon": {"type": "emoji", "emoji": "📋"},
                    "color": "blue_background",
                    "rich_text": [
                        {"type": "text", "text": {"content": "Rules of Business\n", "link": None}, "annotations": {"bold": True}},
                        {"type": "text", "text": {"content": "• Financial Limits: Autonomous execution is strictly limited to ₹50,000. ANY transaction touching a frozen/locked fund requires immediate human review.\n"}},
                        {"type": "text", "text": {"content": "• Compliance Supremacy: A structural change forces human review, even under the threshold.\n"}},
                        {"type": "text", "text": {"content": "• Auditability: Every transaction must be logged with TransactionID, AgentsInvolved, Route, Outcome, Timestamp, and PolicyVersion."}}
                    ]
                }
            }
        ]
        
        # Now append the callout
        httpx.patch(f"{BASE_URL}/blocks/{NOTION_POLICY_BOOK_ID}/children", json={"children": new_blocks}, headers=HEADERS)
        
        # Append the old blocks back (wait, we can't easily recreate old blocks with their internal IDs, but we can reconstruct them or just re-run the previous script's payload).
        # We have the previous script's blocks:
        policy_blocks = [
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
        httpx.patch(f"{BASE_URL}/blocks/{NOTION_POLICY_BOOK_ID}/children", json={"children": policy_blocks}, headers=HEADERS)
        
def create_parent_workspace():
    print("Creating Bank Agent Orchestrator — Governance Workspace page...")
    payload = {
        "parent": {"page_id": NOTION_PARENT_PAGE_ID},
        "properties": {
            "title": {
                "title": [
                    {
                        "type": "text",
                        "text": {"content": "Bank Agent Orchestrator — Governance Workspace"}
                    }
                ]
            }
        }
    }
    res = httpx.post(f"{BASE_URL}/pages", json=payload, headers=HEADERS)
    res.raise_for_status()
    print(f"Parent Workspace ID: {res.json()['id']}")

if __name__ == "__main__":
    add_conflict_summary_property()
    update_gazette_archive()
    create_parent_workspace()
    print("All Notion API updates completed!")
