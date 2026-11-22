# Job Finder

A phone-friendly page listing LinkedIn jobs in planning, project controls, claims and contracts
across Egypt, the Gulf and Italy. It updates itself every morning and is hosted free on GitHub Pages.

## Files

| File | What it does |
|---|---|
| index.html | The website (one page, no libraries). Shortlist, applied and hidden jobs are saved on each device. |
| icon.png | Home Screen icon. |
| job_finder.py | Reads LinkedIn's public job pages. python job_finder.py --test checks LinkedIn still answers. |
| update_jobs.py | Searches every country and keyword group, scores job titles, fetches details and writes site/jobs.json. Keywords, countries and scoring are at the top of this file. |
| .github/workflows/update.yml | Runs the update every morning (04:00 UTC) and publishes the site. |

## Update right away

On GitHub: **Actions → Update jobs → Run workflow → Run workflow**. It takes about 10 minutes.

## Notes

- If LinkedIn blocks a day's run, the site keeps the previous day's jobs.
- To change the page: edit index.html, upload it on GitHub (Add file → Upload files), then run the workflow.
  The site only changes when the workflow runs.
