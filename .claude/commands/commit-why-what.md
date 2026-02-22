Commit the current changes to git.

Steps:

1. Run `git status` and `git diff` (staged and unstaged) to understand what changed.

2. Run `git log --oneline -5` to see recent commit style.

3. Analyze the diff to determine:
   - **Why**: What motivated this change? What problem was being solved or what goal was being achieved?
   - **What**: What was actually done to implement it?

4. Stage the relevant files and commit:
   - The subject line should be the "why" — the motivating reason, in sentence case.
   - The body should be the "what" — a concise explanation of the fix or change.
   - Use a blank line between the subject and body.
