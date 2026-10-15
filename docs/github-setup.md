# GitHub Setup

This workspace was not initialized as a Git repository and no remote was created. First inspect the folder and confirm it is the project you intend to publish. Do not run these commands inside an unrelated repository.

1. Create a **new empty repository** on GitHub. Do not pre-create a README, license, or `.gitignore`; this workspace already has project docs and intentionally leaves the license decision to you.
2. Review `.gitignore`. It excludes `.env`, virtual environments, SQLite files, fetched/run outputs, logs, `node_modules`, and build output. Keep `backend/.env.example` and `frontend/.env.example`; never stage real keys or databases.
3. Initialize only after reviewing the workspace:

```powershell
git init -b main
git status --short
git check-ignore backend/.env backend/data/research.sqlite3 frontend/node_modules
git add .
git status --short
git diff --cached --stat
git diff --cached
```

4. Inspect every staged change and remove any secrets, local results, or files you do not want published. Then commit:

```powershell
git commit -m "Build evidence-based AI research assistant"
```

5. Add the URL of your empty repository, replacing both placeholders, then push:

```powershell
git remote add origin https://github.com/OWNER/REPOSITORY.git
git remote -v
git push -u origin main
```

Choose whether to add a license before publishing. A license grants permissions to others; review its terms and add the corresponding `LICENSE` file intentionally. This build does not choose one for you.
