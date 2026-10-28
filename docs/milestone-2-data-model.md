# Milestone 2 data model: why every column exists

> **Rule:** every column exists to answer a question someone will actually ask, or to keep the system trustworthy.
> Test for any new column: *which question does it answer, and who asks it?* If nobody does, it shouldn't exist.

Related code:
- `app/collector/facts.py`: `PostingFacts` (what the extraction model returns)
- `app/db/models/posting.py`: the new `postings` columns
- `app/db/models/posting_skill.py`: the `posting_skills` table

---

## 1. The problem

After Milestone 1, a posting looks like this:

| title | company | country | description |
|---|---|---|---|
| Machine Learning Engineer | Market Muse | DE | *"…2–5 years of experience… German skills preferred… hybrid/remote options…"* |

A person reading the description understands the job. A database can't: the description is one long string, and SQL can't answer "is this mid-level?" or "does it need German?".

**Milestone 2 turns sentences into columns.** An LLM reads each description once, and its answers are stored as structured values the database can filter, count and sort.

| Group | Columns | Purpose |
|---|---|---|
| A. What the role is | `role_family`, `seniority`, `years_experience_min` | "Is this my kind of job, at my level?" |
| B. Where and how you'd work | `work_mode`, `remote_scope` | "Can I do this from Nigeria?" |
| C. Eligibility | `visa_sponsorship`, `relocation_support`, `clearance_required`, `required_languages`, `preferred_languages` | "Am I allowed to take it?" |
| D. Requirements | `posting_skills` table | "What skills does the market want?" |
| E. Trust | `quality_flag` + extraction bookkeeping | "Is the posting real? Is the extraction right?" |
| F. Provenance (from sources, not the LLM) | `provider`, `external_apply_url`, `applicants` | "Where did this come from? How competitive is it?" |

---

## 2. Group A: what the role is

### `role_family`
Values: `ml_engineer`, `ai_engineer`, `data_scientist`, `data_engineer`, `data_analyst`, `mlops_engineer`, `research_scientist`, `software_engineer`, `other`

- **Why:** titles are chaos. "Machine Learning Engineer", "ML Engineer II", "Machine Learning Specialist", "Engineer, Applied ML" and "KI-Ingenieur" are the same job. `LIKE '%machine learning%'` misses half of them and wrongly matches "Sales Manager, Machine Learning Products". `role_family` collapses every variant into one category.
- **Answers:** "How many ML engineer jobs in the UK this month?", "Is AI engineer demand growing faster than data scientist demand?"
- **Filters noise:** a "Lead Software Engineer – Full Stack" returned by an "AI engineer" search gets `software_engineer` or `other` and stays out of the AI statistics.
- **Why a fixed list:** free text would bring back the chaos ("ML Eng", "Machine-Learning Engineer"). Fixed values give clean `GROUP BY` counts.

### `seniority`
Values: `intern`, `junior`, `mid`, `senior`, `lead`, `unknown`

- **Why:** the most important filter for applying. Titles alone are unreliable: "ML Engineer" with "5+ years required" is effectively senior, so the extractor reads the title and the description.
- **Answers:** "Show me mid-level roles", "What share of AI jobs are senior-only?" (a real market insight).
- **Why `unknown`:** without it the model is forced to guess when the posting doesn't say, and guesses look exactly like facts. `unknown` keeps the data honest.

### `years_experience_min`
Example: `2` for "2–5 years of experience".

- **Why, alongside `seniority`:** "mid" is an interpretation; "2" is a number stated in the posting. Numbers enable precise filters ("3 years or less") and averages. Nullable, because many postings don't state a number.

---

## 3. Group B: where and how you'd work

### `work_mode`
Values: `remote`, `hybrid`, `onsite`, `unknown`

- **Why:** JSearch's `job_is_remote` was `false` for a posting titled "AI Developer (Remote)", so source flags are unreliable and the extractor reads the text. Three states, because "hybrid" is common in Europe and can't be true/false.
- **Two sources:** AnyAPI's LinkedIn rows include `workplaceType`, so those arrive with `work_mode` set. The extractor only fills it in when it's missing: cheaper, and more reliable than the model.

### `remote_scope`
Values: `worldwide`, `region`, `country_only`, `not_applicable`, `unknown`

- **Why:** the most important column for applying from Nigeria. "Remote" means very different things:
  - "Remote (US only)" → cannot apply from Nigeria
  - "Remote, EMEA timezone" → probably can (Nigeria is in EMEA; WAT overlaps with Europe)
  - "Work from anywhere" → can
- **Answers:** "Remote jobs open to someone in Nigeria", a filter `work_mode` alone can't express.

---

## 4. Group C: eligibility

### `visa_sponsorship`, `relocation_support`, `clearance_required`
Values: `yes`, `no`, `not_mentioned`

- `visa_sponsorship`: without it, a role in Germany, the UK, Ireland or Canada can't be taken, however good the match.
- `relocation_support`: whether they help with the move.
- `clearance_required`: the Booz Allen role (US Top Secret) and the UK GenAI role (SC/BPSS) were unobtainable because of this. Detecting it automatically removes them.
- **Why three values, not true/false:** "We do **not** sponsor" means skip; "not mentioned" means worth asking, since many companies sponsor without saying so. True/false would merge them, and you'd skip jobs you could get.

### `required_languages` and `preferred_languages`
Example (Market Muse): required `["English"]`, preferred `["German"]`.

- **Why two lists:** German *required* means unrealistic without fluent German; German *preferred* means you can apply. A single "languages" column would lose exactly this information.
- **Why Postgres arrays, not JSON:** one-line, indexable queries:
  ```sql
  WHERE NOT ('German' = ANY(required_languages))
  ```
  SQLAlchemy 2.1 syntax: `~("German" == any_(Posting.required_languages))` (the older `.any()` method is deprecated).

---

## 5. Group D: skills (a separate table)

### Why skills don't go on `postings`
A posting has one seniority but many skills.

| Option | Problem |
|---|---|
| One text column, `"python, pytorch, docker"` | counting means slow, error-prone text search |
| An array column on `postings` | clumsy counting and grouping; no per-skill details |
| **Separate table, one row per (posting, skill)** | "top skills" is a simple `GROUP BY` ✅ |

This is the classic **one-to-many** relationship.

```sql
-- Top 10 skills for ML engineers in the UK
SELECT ps.skill, count(*) AS postings
FROM posting_skills ps JOIN postings p ON p.id = ps.posting_id
WHERE p.role_family = 'ml_engineer' AND p.country = 'GB' AND p.quality_flag = 'ok'
GROUP BY ps.skill ORDER BY postings DESC LIMIT 10;
```

### `skill` vs `raw_skill`
- `raw_skill`: as written: `"Tensor Flow"`, `"Num Py"`, `"TF2"`
- `skill`: normalized by code: `tensorflow`, `numpy`
- **Why both:** count on `skill`, or one skill appears three times under different spellings. Keep `raw_skill` to audit and improve the alias list:
  `SELECT raw_skill, count(*) FROM posting_skills WHERE skill = 'other' GROUP BY raw_skill;`

### `UniqueConstraint("posting_id", "skill")`
A posting mentioning "Python" three times counts once, so verbose postings don't distort rankings.

### `ondelete="CASCADE"` + `passive_deletes=True`
- Deleting a posting (e.g. a spam cleanup) must delete its skills, or **orphan rows** keep being counted.
- `ondelete="CASCADE"`: Postgres deletes the child rows.
- `passive_deletes=True`: SQLAlchemy leaves it to the database instead of loading and deleting each skill itself.

### `relationship()` on both models
- `posting.skills` → the list of skills; `skill.posting` → its posting; `back_populates` keeps both sides consistent.
- The models refer to each other **by name in quotes** (`"PostingSkill"`, `"Posting"`). SQLAlchemy's registry resolves them once `app/db/models/__init__.py` has imported every model. **Never import one model file from another** (it causes a circular import). For editor type hints, use `if TYPE_CHECKING:` imports.

---

## 6. Group E: quality and trust

### `quality_flag`
Values: `ok`, `suspicious_template`, `not_a_job`, `duplicate_like`, `too_short`

- **Why (from this project's own data):**
  - 6 of 10 jobs in the first Nigeria sample came from one recruiter (Placements24), all one template, for "cutting-edge AI" roles in Bauchi, Lokoja and Minna.
  - The Market Muse posting: generic text, formatting artifacts ("Tensor Flow", "Num Py"), no company-specific detail.
- Counting these as real jobs would make the demand study conclude nonsense ("AI is booming in Bauchi"). Every statistic and recommendation filters `WHERE quality_flag = 'ok'`.
- It also protects the user: the agent won't recommend likely-fake postings.

### Extraction bookkeeping
LLM output is not automatically right. These columns make extraction **checkable, repeatable and improvable**.

| Column | Question it answers | Why it matters |
|---|---|---|
| `extracted_at` | Has this been processed, and when? | `WHERE extracted_at IS NULL` is the **work queue**: safe to re-run, never repeats work or cost |
| `extraction_version` | Which prompt/schema version produced this? | re-extract only old versions after improving the prompt; compare v1 vs v2 |
| `extraction_model` | Which model produced this? | compare models on real data (e.g. the fine-tuned small model in week 6) |
| `extraction_confidence` | How sure was the model? | low-confidence rows go to a **review queue**, not straight into statistics |
| `extraction_evidence` | Why did it decide that? | a short quote backing the answer; quick to audit, and it discourages made-up answers |
| `extraction_error` | Did it fail, and why? | failures are recorded and retryable, never silently lost |

---

## 7. Group F: provenance (from sources, not the LLM)

### `provider`
`rapidapi` (JSearch), `anyapi` (LinkedIn via treg), `direct` (Greenhouse).

- `source` = whose job ID it is (`linkedin`); `provider` = who was paid to fetch it (`anyapi`).
- Find and fix one vendor's bad rows; compare vendors on real data ("AnyAPI rows have descriptions 98% of the time").

### `external_apply_url`
The company's own application page (e.g. `jobs.lever.co/...`). Used by the **application kit**; it also reveals the company's applicant-tracking system, which feeds the Greenhouse/Lever sources.

### `applicants`
Competition: 25 applicants vs 2,000. Lets the agent prioritise ("strong match, only 25 applicants, apply today").

---

## 8. Choices that apply to every column

- **All new columns are nullable.**
  - Existing postings predate them; nullable columns let the migration run without breaking old rows.
  - `NULL` means "not extracted yet"; `unknown` means "extracted, but the posting doesn't say". That difference is what makes `extracted_at IS NULL` a queue.
- **Only frequently filtered columns are indexed** (`role_family`, `seniority`, `work_mode`, `visa_sponsorship`, `quality_flag`, `extracted_at`). Indexes speed up filters but cost storage and insert time; `extraction_evidence` is only read, never filtered.
- **Plain strings, not Postgres ENUM types.** Allowed values are enforced by the `Literal` types in `PostingFacts`. Postgres ENUMs need careful migrations to add values (e.g. a new `llm_engineer` family). Strings plus code validation stay easy to evolve; database `CHECK` constraints can be added later.
- **No numeric limits in the strict output schema.** The SDK's strict mode supports only part of JSON Schema, so limits (e.g. confidence 0–1) are written in the field descriptions and checked by code afterwards.

---

## 9. One posting's journey (Market Muse)

1. **Collected** (`run_daily`): `title`, `company="Market Muse"`, `country="DE"`, `provider="rapidapi"`, `description=…`, `extracted_at=NULL`.
2. **Picked up** by the extractor because `extracted_at IS NULL`; sent to the model with the `PostingFacts` schema.
3. **Facts stored:**
   ```
   role_family="ml_engineer", seniority="mid", years_experience_min=2,
   work_mode="hybrid", remote_scope="unknown", visa_sponsorship="not_mentioned",
   required_languages=["English"], preferred_languages=["German"],
   quality_flag="suspicious_template", extraction_confidence=0.8,
   extraction_evidence="2–5 years of experience… hybrid/remote options",
   extraction_version="v1", extraction_model="<model>", extracted_at=now()
   ```
4. **Skills stored** (normalized by code): `(tensorflow, "Tensor Flow")`, `(numpy, "Num Py")`, `(pytorch, "PyTorch")`, `(python, "Python")`, …
5. **Used later:**
   - `search_jobs`: excluded from "mid-level ML jobs in Germany not requiring German" because `quality_flag != 'ok'`.
   - Analytics chat and demand study: excluded from real-job counts.
   - Review queue: would wait there if confidence had been low.

---

## 10. Interview summary

> "Job descriptions are unstructured text, so I added an LLM extraction stage that converts each posting into typed, filterable facts: role family, seniority, work mode, remote eligibility, visa sponsorship and language requirements, plus a normalized one-to-many skills table for demand analytics. Every extraction is versioned, attributed to a model, confidence-scored and backed by a quoted piece of evidence, so it's auditable and re-runnable. Data quality flags remove templated or spam postings from the statistics, and source-provided fields like LinkedIn's work mode take priority over model guesses."
