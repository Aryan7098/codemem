# Project Documentation Rules

## Mandatory Documentation Standards
For any code changes or architectural updates in this workspace:

1. **`decisions.md` Maintenance:**
   - Log every meaningful decision made during code changes into [decisions.md](file:///c:/Users/azure/Documents/Projects/Graph-Hacks/decisions.md).
   - Document the specific reason and rationale behind each decision (e.g., why this library was chosen, tradeoffs evaluated, why X was selected over Y, and alternatives considered).

2. **`code.md` Maintenance:**
   - Document and keep updated how execution travels between files, functions, and modules in [code.md](file:///c:/Users/azure/Documents/Projects/Graph-Hacks/code.md).
   - Detail what calls what in what order (call chains, data flow pathways).
   - Maintain the active ledger of modified components and file roles.

3. **Communication Protocol:**
   - When asking clarifying questions or eliciting decisions from the user, ask them **one by one**, never all at once.
