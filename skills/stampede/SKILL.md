---
name: stampede
description: "Ask someone else — a PO, designer, developer, QA, or anyone who owns the call — to decide open decisions through a shared HTML page: the requester optionally picks first, the AI recommendation is marked, the decider makes the final pick, and a copy button returns the result. Use when the user types /stampede or $stampede, or asks to get decisions from a PO, designer, or another developer."
argument-hint: "[topic or ticket key]"
---

# Stampede

Collect the decisions you cannot make alone, publish them as one HTML page, and run the
rounds through it. The requester (the user) picks first when that round is wanted, then the
decider stamps over it. Skip the requester round and the page opens on the decider phase
directly.

The page is a single file. Data lives in a JSON file next to it, and a small JS renderer
builds every card from that data. Only the data changes between rounds — the URL does not.

## 1. Collect

First fix who decides. Read the role and the name from the user's request — a PO, a
designer, a developer, a QA, anyone who owns the call. When you cannot tell, ask: in Claude
Code use AskUserQuestion; in Codex ask in plain text and wait for the answer. Record them as
`meta.decider_role` (for example "PO", "디자이너", "개발자", "QA") and `meta.decider` (the
name). The role is what the page says on screen, so use the wording the team uses.

Then gather open decisions from three places:

- **Design docs** — open questions and undecided items in a PRD, a design spec, a Figma
  file, an API contract, or an ADR (`origin: "prd"`, or `origin: "doc"` with an
  `origin_label` such as "Figma" or "API 명세" when the chip should name the document type)
- **Tickets** — unresolved items on the ticket (`origin: "jira"`)
- **This session** — blockers you hit that only the user or the decider can settle
  (`origin: "session"`)

For each decision record:

- `origin` and at least one source: a `path:line`, a ticket key, or the literal "this session"
- `background` — the paragraphs that let someone decide without reopening the source
- 2–4 options, each with the `effect` of picking it
- one `recommended` option and a one-or-two sentence `rationale`
- `allow_custom: true` when the answer might not be on the list (adds a custom-input option)

Write the background in the decider's own terms: for a designer, the screen and interaction
impact; for a PO, the user and business impact; for a developer, the implementation and
operations impact.

Leave out anything already decided. A page with three real decisions beats one with twelve.

## 2. Write data

Write `~/.stampede/<name>/decisions.json`, with `<name>` lowercase letters, digits and
hyphens. That directory is the state directory; everything else is written beside it.

The schema is in `example.json`. The only field that changes between rounds is
`requester_picks` (plus `requester_memos` and `requester_note`): **empty or absent renders
the requester phase, present renders the decider phase.**

Set `meta.lang` to `ko` (default) or `en` to pick the language of everything the page says —
titles, buttons, the lock screen and the copied text. Choose the language the decider reads.

Set `"skip_requester": true` when there is no requester round. The page then opens on the
decider phase from the start, with no requester column, and the decider reply records no
`diff_from_requester`.

A decision may carry optional `copy_blocks` — `[{"label": ..., "text": ...}]`. Use them for
text the other side has to paste back as written: a template message, a draft of the
request. Each block shows up below the background with its own copy button, and the blocks
stay out of the copied result.

Keep the previous round's answers in `requester_draft` —
`{"picks": ..., "memos": ..., "note": ...}` — when you add a question and bump the version:
it pre-fills the requester page so the requester does not pick the same answers again. Once
`apply` records the requester reply, the draft is gone.

If the state directory already exists and the questions changed, bump `meta.version`.
A reply carrying an older version is refused, so stale pages cannot corrupt the data.

## 3. Ask, then publish

Before publishing, ask the user whether the requester picks first. In Claude Code use
AskUserQuestion; in Codex ask in plain text and wait for the answer.

The requester is the user, on this machine, so that round does not go to a shared host. Leave
`skip_requester` out and open the page locally instead — the local server also takes the
answer back, so nothing is copied or pasted. The page it opens carries a **요청자 확인**
(`Confirm as requester`) button, and pressing it hands the reply straight to the agent:

```bash
python3 <skill-dir>/serve.py --dir ~/.stampede/<name>
```

In Claude Code start that command with `run_in_background` and give the user the `serving:`
URL from its first line. Inside Orca (`TERM_PROGRAM=Orca`) open that URL as a browser tab with
`orca tab create --url <url>`; if the orca CLI is not there, just give the user the URL. In
Codex run the command in the foreground and wait for it to end.

The page ends the wait by itself. When the process prints `submitted:` and exits 0, the reply
is in `<dir>/requester-reply.txt`: apply it and publish the decider phase right away.

```bash
<skill-dir>/render.py apply ~/.stampede/<name>/decisions.json ~/.stampede/<name>/requester-reply.txt
```

If it ends with `timeout` and exit 3 instead, nobody submitted — ask the user whether to open
it again. A pasted reply still works: apply it as in section 4, since the page keeps the copy
button next to the send button as the fallback.

When the user does not want a requester round, add `"skip_requester": true` to the data first,
and the publish below goes straight to the decider phase. That page is shared, so it is
published as it always was.

```bash
<skill-dir>/deliver.sh --name <name> --dir ~/.stampede/<name>
```

### Publishing channels

`deliver.sh` tries the user's own publishers first, then Orca, then a Claude artifact. Report
any `skip: <channel> <reason>` line in one line, so a silent downgrade is visible.

**Publishers.** Executable files in `${STAMPEDE_PUBLISHERS_DIR:-$HOME/.config/stampede/publishers}`
are tried in filename order (`10-intranet.sh`, `20-team-wiki.sh`). A missing or empty
directory skips the whole step silently. Each publisher is called as:

```
<publisher> --name <name> --dir <state dir> --page <page.html> --locked-page <page-locked.html> --pin <NNNN>
```

It chooses for itself whether to publish the locked or the unlocked page — a host that only
the internal network can reach is fine unlocked. Success is exit code 0 with `delivered: <url>`
as the last line of stdout (add ` pin=NNNN` when the published page is PIN-locked). Any other
exit is a failure; when its last line is `skip: <reason>` that reason is passed through.

If a reason carries `(run: <command>)`, the publisher is telling you the dependency needs a
one-time step — a login, a VPN. Run that command, show the user what it asks for and wait for
their approval, then run `deliver.sh` once more with `--start publisher:<file>`. Never
automate the login.

**Built-in channels.** When no publisher delivers, the same run falls through to an Orca
artifact and then to a locked local file. Branch on the last line of stdout:

- `delivered: publisher:<file> <url>` — send the URL as is (with the PIN if one is printed).
- `delivered: orca-artifact <url> pin=NNNN` — send the URL and the PIN.
- `fallback: claude-artifact <file> pin=NNNN` — publish that locked file with the Claude
  Code `Artifact` tool (icon `checklist`) and send the resulting link plus the PIN. In Codex
  there is no Artifact tool: give the user the local path and the PIN and stop there.

Orca and Claude artifacts are always PIN-locked; a publisher is locked only when it says so.

If the page you published is already the decider phase, it is finished work: tell the user
the decider is ready to share — the link and the PIN if there is one — and stop. Sending it
to the decider is the user's step, so do not draft the message for them.

## 4. Requester reply

The local server normally applies the reply on its own (section 3): it writes
`<dir>/requester-reply.txt` and the agent runs `apply` on that file. This section is the
fallback for when the page could not reach the server and the user pasted the copied text
into the chat. Save it to a scratch file and run:

```bash
<skill-dir>/render.py apply ~/.stampede/<name>/decisions.json <file>
```

Then publish again with the same `--name` and `--dir` and no `--start`. `deliver.sh` reads
the channel and PIN back from `state.json`, so the requester keeps the same link and PIN, and
the same publisher is tried first. For a Claude artifact, run the `Artifact` tool again on the
same path (`<dir>/page-locked.html`) so the URL does not change. If a `skip:` line means the
channel fell back to a different one, tell the user the new link and PIN. The page now opens
on the decider phase: tell the user the decider is ready to share — the link and the PIN if
there is one — and stop; sending it to the decider is the user's step. A requester reply is
not a decider decision, so nothing is settled until the decider's own reply arrives.

## 5. Decider reply

Apply the pasted text the same way. For a decider reply this writes `decision-result.json`
beside `decisions.json` and prints the final label per item.

Summarize the items where the decider differed from the requester first, or, when the
requester round was skipped (`skip_requester`), the decider picks as they are; then **continue
the work that was blocked** — update the spec or the ticket, unblock the session, and say
which decision moved. If `apply` exits 2 with a version mismatch, do not apply it; tell the
user the page and the reply are different versions.

## Limits

- The PIN is four digits, so it only resists casual access, not offline brute force. Never
  put customer personal data or secrets on a page, locked or not.
- `render.py` is standard-library-only. `lock.mjs` needs node (v26, built-in `webcrypto`).
  `render --pin` calls it through a subprocess.
- A reply from a page published before the decider phase was renamed still carries the old
  `po` phase, and data written then carries `meta.po` instead of `meta.decider`. Both are
  accepted: `po` is read as the decider phase, and `meta.po` is read as the decider name with
  the role "PO".

## Files

| File | Role |
|---|---|
| `SKILL.md` | This procedure |
| `template.html` | Page template; data is injected as JSON and rendered by JS |
| `render.py` | `render` and `apply` subcommands |
| `serve.py` | Opens the requester page on 127.0.0.1 and writes the reply it submits |
| `lock.mjs` | PBKDF2-SHA256 → AES-GCM-256 encryption of the data blob |
| `deliver.sh` | Publish chain: user publishers → orca artifact → Claude artifact |
| `example.json` | Example data (a multi-part decision, a session origin, a custom-input decision) |
| `test_render.py`, `test_serve.py`, `test_deliver.sh` | Unit tests and stubbed publish-chain tests |
