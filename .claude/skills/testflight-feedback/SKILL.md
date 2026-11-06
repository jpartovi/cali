---
name: testflight-feedback
description: Pull TestFlight beta feedback (tester comments, screenshots, crash reports) for Cali ("Cali Social" in App Store Connect, bundle com.cali-inc.Cali), triage it into work items, and fix the approved ones in ~/cali. Use when the user asks to check, gather, triage or work through TestFlight / beta / tester feedback for Cali.
---

# TestFlight feedback → fixes (Cali Social)

The repo is **`~/cali`**. The app is listed in App Store Connect as **Cali Social** (bundle ID `com.cali-inc.Cali`).
- Use absolute paths, or `cd ~/cali &&` at the start of each command.
- Other sessions may share this checkout. Before editing, check `git status` and `git log origin/main..HEAD`, and never touch uncommitted changes you didn't make.
- Work on the current branch unless the user says otherwise.

## 1. Fetch

```bash
cd ~/cali && python3 scripts/testflight_feedback.py
```

- Writes `~/cali/.local/testflight-feedback/feedback.md` (only items not yet handled), plus `feedback.json`, `screenshots/` and `crashlogs/`. `.local/` is gitignored.
- Credentials come from `~/.appstoreconnect/config.json` (`ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_PATH`), the same key the Backgammon app uses. If they're missing, ask the user for the Key ID and Issuer ID, which are under App Store Connect → Users and Access → Integrations → App Store Connect API. Never print the `.p8` contents.
- Needs PyJWT with crypto (`pip install 'pyjwt[crypto]'`).
- If the script reports 0 unhandled items, say so and stop.

## 2. Understand every item

- Read each comment, the build number and the device.
- **Open every screenshot with the Read tool.** Testers often point at the problem visually and leave very short comments.
- For crashes, read the `.crash` file and find the top frames in the app's own code (the `Cali` module).
- Many reports are about the agent's behavior rather than the UI. Find the relevant code under `agent/` or `backend/`, and look up LangSmith traces or `agent_ratings` rows from around the report time when that helps.
- Check whether the current code already fixes the item, since newer builds often have. Compare against `git log` and the code. Mark items that are already fixed instead of redoing them.

## 3. Triage, then ask

Merge duplicates (one work item that lists every submission ID it covers). Classify each item as **crash**, **bug**, **UX/polish**, **agent behavior**, **feature request**, **already fixed** or **not actionable**.

Show the user a compact table:

| # | Type | Summary | Reports | Builds | Likely area |
|---|------|---------|---------|--------|-------------|

Ask which items to tackle and how they want any open questions resolved. Don't start fixing until they answer.

## 4. Fix, one item at a time

For each approved item:
1. Find the root cause and make the smallest correct fix, matching the surrounding style.
2. If the feedback is ambiguous or needs a product decision, stop and ask the user.

Do the work yourself, one item after another. Use subagents only for read-only investigation, or for edits to files that don't overlap.

## 5. Verify

- iOS: `xcodebuild -project ios/Cali.xcodeproj -scheme Cali -sdk iphonesimulator -configuration Debug CODE_SIGNING_ALLOWED=NO build`, and check UI changes visually in the simulator.
- Backend/agent: run the relevant tests under `backend/` and `agent/`.

## 6. Wrap up

1. Record each handled submission:
   ```bash
   cd ~/cali && python3 scripts/testflight_feedback.py --mark <submission-id> fixed "<sha> <summary>"
   ```
   Use `wontfix`, `duplicate`, `already-fixed` or `needs-decision` as appropriate.
2. Summarize each item in one line: what changed, how you verified it, and what still needs a device test.
3. **Ask before committing and pushing.**
4. Testers only get iOS fixes in a new TestFlight build, which needs a new, higher build number and an upload (archive and export with the Xcode-signed-in account; don't pass the API key flags). Backend and agent fixes reach testers once they're deployed.
