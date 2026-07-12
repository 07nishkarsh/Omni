# Section I: Standard Loan Routing
- **Clause 1**: Standard loan routing follows a strict serial flow (A -> B -> C). Subsidy loans carry additional compliance rules. A verified guarantor can offset a primary risk flag. Disbursement-splitting rules must be evaluated prior to final approval.
- **Clause 2**: Under emergency or disaster bypass conditions, routing flows directly from Agent A to Agent C for expedited execution, while Agent B monitors asynchronously.
- **Clause 3**: Autonomous execution is strictly limited to an explicit threshold of ₹50,000.
- **Clause 4**: ANY transaction touching a frozen or locked fund requires immediate human review, regardless of the transaction amount.

# Section II: Negotiation Constraints
- **Clause 1**: Automated negotiations are capped at a maximum of three (3) rounds.
- **Clause 2**: A "structural change" (e.g., changes to term length, collateral type, or interest rate tier) forces human review, even if the proposed amount is under the autonomous threshold.

# Section III: Audit Requirements
- **Clause 1**: Every transaction must be logged with the following fields: TransactionID, AgentsInvolved, Route, Outcome, Timestamp, and PolicyVersion.

