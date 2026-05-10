# ED-Alpha Static Demo

This directory contains the static ED-Alpha demo site used for GitHub Pages. It is separate from the live Docker app under `app/`: this demo reads from `data/demo-data.json` and does not require the FastAPI backend or Postgres.

## Routes

- `/` shows the ED-Alpha landing page with the embedded MP4 walkthrough.
- `/tutorial/` shows the interactive dashboard tutorial.
- `/walkthrough/` shows the timed HTML walkthrough with playback controls, subtitles, highlights, and narration.
- `/demo-video/` keeps the same walkthrough available for older links.

## Local Development

```bash
corepack enable
pnpm install
pnpm dev
```

Open http://localhost:3000.

## Static Build

```bash
pnpm build
```

The build writes static files to `out/`. The app is configured with `output: "export"` and includes `.nojekyll` so it can be hosted on GitHub Pages.

For project Pages, build with the repository base path:

```bash
NEXT_PUBLIC_BASE_PATH=/ED-ALPHA pnpm build
```

For a custom domain at the site root, leave `NEXT_PUBLIC_BASE_PATH` unset.
