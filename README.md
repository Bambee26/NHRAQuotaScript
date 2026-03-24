# NHRA quota watcher (GitHub Actions ready)

Files included:
- `nhra_github_script.py`
- `requirements.txt`
- `.github/workflows/nhra-quota.yml`

## What it does
- Opens the NHRA event status page
- Checks future/today events only
- Looks for the `Super Comp` row
- Reads the exact NHRA table columns:
  - Category
  - Quota
  - Entries
  - % Full
- Sends a text through your carrier's email-to-text gateway when entries drop below quota

## GitHub setup
1. Create a GitHub repo.
2. Upload all files from this folder.
3. In GitHub, go to:
   Settings -> Secrets and variables -> Actions
4. Add these repository secrets:
   - `SMTP_HOST`
   - `SMTP_PORT`
   - `SMTP_USERNAME`
   - `SMTP_PASSWORD`
   - `EMAIL_FROM`
   - `EMAIL_TO`

## Suggested secret values
Use your own current values in GitHub Secrets, not in the code.

## Run schedule
The workflow is set to run every 15 minutes.
