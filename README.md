# internship-watch

Polls three internship-listing repos every 20 min via GitHub Actions and pushes
new matching postings to my phone via ntfy.sh. Runs on GitHub's servers — no
local machine required.

| source | format | notes |
|---|---|---|
| SimplifyJobs/Summer2027-Internships | JSON + ETag | 16k listings, base layer |
| vanshb03/Summer2027-Internships | JSON + ETag | uses `season`, no `category` |
| speedyapply/2027-SWE-College-Jobs | markdown | salary data, ~50 unique companies |

Dedupe is cross-source on normalized company+title (each repo assigns its own ids).
Markdown sources have a row-count guard: if a README restyle drops rows below 50%
of the last known count, the run refuses to update state and sends a high-priority
alert instead of silently going quiet.

Term/location filters live in `.github/workflows/watch.yml` (TERMS / LOCATIONS; CATEGORIES there is unused).
Role filter (SWE + design only) is `ROLE_ALLOW` / `ROLE_BLOCK` in `watch.py`.
`SEED=1 python3 watch.py` marks everything current as seen without notifying.
