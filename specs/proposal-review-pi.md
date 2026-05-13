# PI Proposal Evaluation Specification

## Overview

PIs receive collaboration proposals through two independent pathways: the multi-agent Slack simulation (`ThreadDecision`) and the admin-initiated Matchmaker (`MatchmakerProposal`). This feature surfaces all proposals involving a given PI in a single unified list and provides a structured evaluation form using NIH-style 1–9 scoring. The origin of each proposal (agent vs. matchmaker) is never revealed to the evaluator; row order is randomized on each page load to further obscure provenance.

Evaluations are stored in a new `pi_proposal_evaluations` table, separate from the existing `proposal_reviews` table (the 1–4 agent-blocking system). The two systems coexist independently — a PI may submit an evaluation here without affecting or replacing the 1–4 agent review workflow.

---

## New Page: Proposal Evaluations (`/proposals`)

### Access Control

- Requires an authenticated user with `access_status = "allowed"`.
- Admins see proposals for all users (optional; see Admin Note below). Regular users see only proposals in which they appear as PI A or PI B.

### Route

```
GET /proposals
```

A top-level nav entry alongside the agent dashboard link. Not nested under `/agent/{agent_id}/` because it is user-level (not per-agent) and aggregates both proposal types.

---

### Proposal List

#### Data Query

Fetch two sets of proposals for `current_user.id`, then merge:

**Agent proposals** — `ThreadDecision` where `outcome = "proposal"` and:
```
agent_a ∈ {agent_ids for current_user}   OR
agent_b ∈ {agent_ids for current_user}
```
Join `AgentRegistry` on `agent_id → user_id` to resolve ownership.

**Matchmaker proposals** — `MatchmakerProposal` where:
```
pi_a_id = current_user.id   OR
pi_b_id = current_user.id
```

Merge both sets into a single list. Assign each item a stable `display_type` of `"agent"` or `"matchmaker"` for internal routing only — never rendered in the UI.

#### Row Order

Shuffle the merged list with a **per-request random seed** (Python `random.shuffle`) on every page load. Do not persist the order; do not use any stable sort column (not `generated_at`, not alphabetical). The goal is to prevent a PI from inferring origin from consistent ordering.

#### List Layout

Each row is a card or table row containing:

| Element | Notes |
|---|---|
| **Title** | `MatchmakerProposal.title` or first non-empty heading extracted from `ThreadDecision.summary_text` |
| **Collaborator** | Name of the other PI (not the current user). For agent proposals, resolve via `AgentRegistry → User.name`; for matchmaker, use `pi_a_name`/`pi_b_name` or `User.name` from the FK. |
| **Status badge** | `Pending Evaluation` (gray) or `Evaluated` (green), based on whether a `PiProposalEvaluation` exists for this user + proposal. |
| **Action** | "Evaluate" → links to the evaluation form. "View" if already evaluated (can re-open read-only, or allow amendment — see Amendments below). |

No date, no confidence label, no source indicator. Keep the row minimal to avoid inadvertently leaking origin.

#### Empty State

If the user has no proposals: display a short message — "No collaboration proposals yet. Proposals will appear here once your agent has completed discussions or the admin has generated a matchmaker proposal involving you."

---

## Evaluation Form (`/proposals/{unified_id}/evaluate`)

### URL Design

Use a **unified identifier** that does not encode origin. Options:

- Encode `type:uuid` as a base64 URL-safe token (e.g., `bWF0Y2htYWtlcjo8dXVpZD4=`), or
- Use a hash-derived short ID stored in a lookup table.

Recommended: encode as `{type_prefix}_{uuid_hex}` without separators (e.g., `a_3f8c...` vs. `m_9d2e...`), then base64url-encode the whole string so the type is not human-readable in the browser bar.

The backend decodes the token, validates the user has access to that proposal, then renders the form.

### Proposal Summary

At the top of the form, display the full proposal text. For agent proposals use `summary_text`; for matchmaker use `proposal_md` rendered as markdown. Do **not** include metadata that reveals origin (no "Confidence" badge, no agent names, no Slack channel). Show only:

- **Title** (heading)
- **Collaborator name**
- **Proposal body** (full markdown)

---

### NIH-Style Scoring Sections

The form has **six scored sections**: five criterion scores (each 1–9) and one Overall Impact score (1–9).

#### Scoring Guide (displayed inline at top of form, collapsed by default)

> **How to Score: NIH 1–9 Scale**
>
> Scores are whole numbers from **1 (best) to 9 (worst)**. Use the full range.
>
> | Score | Descriptor | Strengths / Weaknesses |
> |---|---|---|
> | 1 | **Exceptional** | Essentially no weaknesses |
> | 2 | **Outstanding** | Negligible weaknesses |
> | 3 | **Excellent** | Only minor weaknesses |
> | 4 | **Very Good** | Numerous minor weaknesses |
> | 5 | **Good** | At least one moderate weakness |
> | 6 | **Satisfactory** | Some moderate weaknesses |
> | 7 | **Fair** | At least one major weakness |
> | 8 | **Marginal** | A few major weaknesses |
> | 9 | **Poor** | Numerous major weaknesses |
>
> **Weakness severity:**
> - *Minor* — easily addressable; does not substantially lessen impact
> - *Moderate* — lessens impact
> - *Major* — severely limits impact
>
> Scores of 1 and 9 are expected to be rare. Scores of 1–3 indicate high impact; 4–6 moderate; 7–9 low.
>
> The **Overall Impact** score is your holistic judgment of the likelihood that this collaboration will exert a sustained, powerful influence on the field. It is *not* the average of the five criteria — weigh them as you see fit.

This guide is collapsed behind a "Show scoring instructions ▾" toggle so experienced evaluators can skip it.

---

#### Section 1: Significance (score 1–9)

**Label:** Significance

**Prompt:**
> How important is the proposed collaboration to the field? Does it address a significant gap in knowledge, solve a critical problem, or represent an advance that would benefit the broader research community? Assume the proposed work will be successfully completed.

**Input:** Dropdown or radio buttons 1–9 with descriptor labels (Exceptional … Poor). Plus an optional free-text **Comments** field (placeholder: "What are the key strengths or weaknesses in significance?").

---

#### Section 2: Innovation (score 1–9)

**Label:** Innovation

**Prompt:**
> Does the proposed collaboration apply novel concepts, approaches, methodologies, or technologies? Does it combine the PIs' expertise in a genuinely new way, or apply existing methods in an innovative context?

**Input:** Dropdown/radio 1–9 + optional Comments.

---

#### Section 3: Approach (score 1–9)

**Label:** Approach

**Prompt:**
> Is the proposed plan of work sound and achievable? Are the scientific rationale and methods appropriate? Does the proposal address potential challenges or risks? Are the experimental designs or research strategies rigorous?

**Input:** Dropdown/radio 1–9 + optional Comments.

---

#### Section 4: Investigators (score 1–9)

**Label:** Investigators

**Prompt:**
> Do the collaborating PIs have the background, training, and complementary expertise needed to execute this work? Is the combination of their research programs well-suited to the proposed goals?

**Input:** Dropdown/radio 1–9 + optional Comments.

---

#### Section 5: Environment (score 1–9)

**Label:** Environment

**Prompt:**
> Are the institutional resources, facilities, and collaborative infrastructure available to support this work? Would the combination of the two labs' environments enhance the likelihood of success?

**Input:** Dropdown/radio 1–9 + optional Comments.

---

#### Section 6: Overall Impact (score 1–9)

**Label:** Overall Impact Score

**Prompt:**
> Provide your overall assessment of the likelihood that this collaboration would exert a **sustained, powerful influence** on the research field(s) involved. Weigh the five criteria above as you see fit — the overall impact score is not an average. A proposal need not be strong in all criteria to earn a high impact score.

**Input:** Dropdown/radio 1–9 + required **Overall Comments** field (at least one sentence required before submission).

---

### Form Submission

**Button:** "Submit Evaluation"

On submit:
1. Validate all six scores are provided (1–9) and Overall Comments is non-empty.
2. POST to `POST /proposals/{unified_id}/evaluate`.
3. Insert a `PiProposalEvaluation` row (see Data Model below).
4. Redirect to `/proposals` with a success flash: "Evaluation submitted."

If the user has already submitted an evaluation for this proposal, the form pre-fills with their prior scores and comments and shows an "Update Evaluation" button that overwrites the existing row (upsert on `user_id + proposal_key`).

---

## Data Model

### New Table: `pi_proposal_evaluations`

```
pi_proposal_evaluations
───────────────────────────────────────────────────────
id                      UUID  PK  default gen_random_uuid()
user_id                 UUID  FK → users.id  NOT NULL
proposal_type           VARCHAR(20) NOT NULL  -- "agent" | "matchmaker"
thread_decision_id      UUID  FK → thread_decisions.id  NULLABLE
matchmaker_proposal_id  UUID  FK → matchmaker_proposals.id  NULLABLE
score_significance      SMALLINT NOT NULL  -- 1–9
score_innovation        SMALLINT NOT NULL  -- 1–9
score_approach          SMALLINT NOT NULL  -- 1–9
score_investigators     SMALLINT NOT NULL  -- 1–9
score_environment       SMALLINT NOT NULL  -- 1–9
score_overall_impact    SMALLINT NOT NULL  -- 1–9
comments_significance   TEXT  NULLABLE
comments_innovation     TEXT  NULLABLE
comments_approach       TEXT  NULLABLE
comments_investigators  TEXT  NULLABLE
comments_environment    TEXT  NULLABLE
comments_overall        TEXT  NOT NULL
evaluated_at            TIMESTAMP  NOT NULL  default now()
updated_at              TIMESTAMP  NULLABLE  -- set on amendment
```

**Constraints:**
- `CHECK (proposal_type IN ('agent', 'matchmaker'))`
- `CHECK (score_significance BETWEEN 1 AND 9)` (and same for all five criteria + overall)
- `CHECK (thread_decision_id IS NOT NULL OR matchmaker_proposal_id IS NOT NULL)` — exactly one must be set
- `UNIQUE (user_id, thread_decision_id)` where `thread_decision_id IS NOT NULL`
- `UNIQUE (user_id, matchmaker_proposal_id)` where `matchmaker_proposal_id IS NOT NULL`

**Indexes:**
- `(user_id, proposal_type)` — for the list page query
- `(thread_decision_id)` — for admin aggregation
- `(matchmaker_proposal_id)` — for admin aggregation

### Why a Separate Table from `proposal_reviews`

`proposal_reviews` is tightly coupled to the agent-blocking workflow: it has a unique constraint on `(thread_decision_id, agent_id)`, drives the "pending proposals" gate in the simulation, and uses a 1–4 scale designed around the agent system's needs. The NIH evaluation is a research-quality instrument for a different purpose (comparative assessment, future blinded studies), and the two systems should evolve independently.

---

## Backend Routes

### `GET /proposals`
- Auth: `get_current_user` (access_status = "allowed")
- Query: merged list of agent + matchmaker proposals involving `current_user.id`
- Annotate each with `evaluated: bool` by checking `pi_proposal_evaluations`
- Shuffle with `random.shuffle` before passing to template
- Template: `proposals/list.html`

### `GET /proposals/{token}/evaluate`
- Decode token → `(proposal_type, proposal_id)`
- Validate current user is PI A or PI B for this proposal (403 otherwise)
- Fetch proposal content; pre-fill existing evaluation if present
- Template: `proposals/evaluate.html`

### `POST /proposals/{token}/evaluate`
- Decode token; validate access
- Validate form fields (all six scores present, overall comment non-empty, scores in 1–9)
- Upsert `PiProposalEvaluation` (insert or update if row already exists for this user + proposal)
- Redirect to `/proposals` with flash message

---

## Templates

### `templates/proposals/list.html`

Extends base layout. Nav highlight on "Proposals".

Structure:
```
<h1>Collaboration Proposals</h1>
<p class="subtitle">Proposals involving your lab for your evaluation.</p>

[proposal cards, shuffled]

Each card:
  Title
  With: [Collaborator Name]
  [Status badge]
  [Evaluate / View button]
```

No columns that would reveal origin. No sort controls (sort is intentionally hidden).

### `templates/proposals/evaluate.html`

Extends base layout.

Structure:
```
<h1>[Proposal Title]</h1>
<p>Proposed collaboration with <strong>[Collaborator Name]</strong></p>

[Full proposal body, rendered markdown]

<hr>

<h2>Your Evaluation</h2>

<details>
  <summary>Show scoring instructions ▾</summary>
  [Scoring guide table]
</details>

[Section 1: Significance]
[Section 2: Innovation]
[Section 3: Approach]
[Section 4: Investigators]
[Section 5: Environment]
[Section 6: Overall Impact + required comment]

[Submit / Update button]
```

Score inputs: use a horizontal radio button group (1–9) with descriptor labels below (Exceptional … Poor), similar to a Likert scale widget. This makes the scale's direction immediately obvious.

---

## Admin Page: PI Proposal Evaluations (`/admin/evaluations`)

Full admin visibility into all PI evaluation submissions. Admins see the `proposal_type` column (agent vs. matchmaker) — this is the only place in the system where origin is revealed. See `admin-dashboard.md` §12 for the canonical specification; a summary is included here for reference.

### Layout

**Summary cards (top of page):**
- Total evaluations submitted
- Evaluations this month
- Proposals with at least one evaluation (vs. total proposals)
- Mean overall impact score (all time, shown as X.X / 9)

**Main table — one row per `PiProposalEvaluation`:**

| Column | Notes |
|---|---|
| **Evaluator** | `User.name` who submitted the evaluation |
| **Proposal title** | Title from the linked proposal |
| **Origin** | `Agent` or `Matchmaker` badge — color-coded (blue / purple). This is the only UI surface in the system that reveals origin. |
| **Other PI** | Name of the collaborator (the PI who is *not* the evaluator) |
| **Sig.** | `score_significance` (1–9) |
| **Inn.** | `score_innovation` (1–9) |
| **App.** | `score_approach` (1–9) |
| **Inv.** | `score_investigators` (1–9) |
| **Env.** | `score_environment` (1–9) |
| **Impact** | `score_overall_impact` (1–9), emphasized (bold) |
| **Submitted** | `evaluated_at` timestamp |
| **Actions** | "View details" → modal or expanded row showing all comments |

Scores are color-coded: 1–3 green (high impact), 4–6 yellow, 7–9 red.

**Detail view (row expand or modal):**
- All six criterion scores with their comments
- Full proposal body (rendered markdown)
- Evaluator name, submission timestamp, amendment timestamp if updated

### Filters

- **Evaluator** — user multi-select
- **Origin** — All / Agent only / Matchmaker only
- **Overall Impact range** — min/max sliders (1–9)
- **Date range** — evaluated_at from/to

### JSON Export

`GET /admin/evaluations/export.json` — streams a JSON file of all `PiProposalEvaluation` rows matching the current filter state (filters passed as query params, same as page view).

**Schema:**

```json
{
  "exported_at": "2026-05-04T09:00:00Z",
  "total_records": 42,
  "filters_applied": {
    "origin": "all",
    "evaluator_ids": [],
    "impact_score_min": null,
    "impact_score_max": null,
    "date_from": null,
    "date_to": null
  },
  "evaluations": [
    {
      "evaluation_id": "uuid",
      "evaluated_at": "2026-04-15T14:32:00Z",
      "updated_at": null,
      "evaluator": {
        "user_id": "uuid",
        "name": "Jane Smith",
        "orcid": "0000-0002-1234-5678",
        "institution": "Scripps Research"
      },
      "proposal": {
        "origin": "agent",
        "proposal_id": "uuid",
        "title": "Cryo-ET and Proteomics of Mitochondrial Dynamics",
        "collaborator": {
          "user_id": "uuid",
          "name": "Michael Wiseman",
          "institution": "Scripps Research"
        }
      },
      "scores": {
        "significance": 2,
        "innovation": 3,
        "approach": 4,
        "investigators": 2,
        "environment": 2,
        "overall_impact": 2
      },
      "comments": {
        "significance": "Addresses a genuine gap in understanding...",
        "innovation": null,
        "approach": "Feasibility is somewhat unclear for aim 3...",
        "investigators": null,
        "environment": null,
        "overall": "Strong proposal with highly complementary expertise. Minor concerns about timeline."
      }
    }
  ]
}
```

**Implementation note:** Build the export from the same query used to render the table — apply the same filters so the downloaded file always matches what the admin sees on screen. The export is synchronous (no background job) at pilot scale.

---

## Alembic Migration

New migration: `0016_add_pi_proposal_evaluations.py` (or next available revision).

```python
op.create_table(
    "pi_proposal_evaluations",
    sa.Column("id", postgresql.UUID(as_uuid=True), ...),
    sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("proposal_type", sa.String(20), nullable=False),
    sa.Column("thread_decision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("thread_decisions.id"), nullable=True),
    sa.Column("matchmaker_proposal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("matchmaker_proposals.id"), nullable=True),
    sa.Column("score_significance", sa.SmallInteger(), nullable=False),
    sa.Column("score_innovation", sa.SmallInteger(), nullable=False),
    sa.Column("score_approach", sa.SmallInteger(), nullable=False),
    sa.Column("score_investigators", sa.SmallInteger(), nullable=False),
    sa.Column("score_environment", sa.SmallInteger(), nullable=False),
    sa.Column("score_overall_impact", sa.SmallInteger(), nullable=False),
    sa.Column("comments_significance", sa.Text(), nullable=True),
    sa.Column("comments_innovation", sa.Text(), nullable=True),
    sa.Column("comments_approach", sa.Text(), nullable=True),
    sa.Column("comments_investigators", sa.Text(), nullable=True),
    sa.Column("comments_environment", sa.Text(), nullable=True),
    sa.Column("comments_overall", sa.Text(), nullable=False),
    sa.Column("evaluated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
)
op.create_check_constraint("ck_ppe_proposal_type", "pi_proposal_evaluations", "proposal_type IN ('agent', 'matchmaker')")
op.create_check_constraint("ck_ppe_proposal_present", "pi_proposal_evaluations",
    "thread_decision_id IS NOT NULL OR matchmaker_proposal_id IS NOT NULL")
# Score range checks
for col in ["significance", "innovation", "approach", "investigators", "environment", "overall_impact"]:
    op.create_check_constraint(f"ck_ppe_score_{col}", "pi_proposal_evaluations",
        f"score_{col} BETWEEN 1 AND 9")
op.create_unique_constraint("uq_ppe_user_thread", "pi_proposal_evaluations",
    ["user_id", "thread_decision_id"])
op.create_unique_constraint("uq_ppe_user_matchmaker", "pi_proposal_evaluations",
    ["user_id", "matchmaker_proposal_id"])
op.create_index("ix_ppe_user_type", "pi_proposal_evaluations", ["user_id", "proposal_type"])
op.create_index("ix_ppe_thread", "pi_proposal_evaluations", ["thread_decision_id"])
op.create_index("ix_ppe_matchmaker", "pi_proposal_evaluations", ["matchmaker_proposal_id"])
```

---

## Open Questions

1. **Token encoding**: Prefer base64url opaque token or a human-readable `{type}/{uuid}` path? The latter is simpler to implement but makes origin obvious in the URL bar. Recommend opaque token.
2. **Re-evaluation policy**: Allow PIs to update their evaluation after submission (upsert)? Or lock after first submission? Current spec allows amendment; change if a one-shot design is preferred for the study.
3. **Matchmaker proposals with no `pi_a_id`/`pi_b_id`** (CLI-created, name-only rows): These cannot be linked to a specific `user_id`. Either exclude them from the PI list view or require admin to backfill FKs before they appear. Recommend exclusion with a note in the admin UI.
4. **Delegate access**: Should delegates be able to submit evaluations on behalf of the PI? The existing delegate model (`web-delegates.md`) could extend here but is out of scope for v1 — delegates see only the existing 1–4 review workflow.
