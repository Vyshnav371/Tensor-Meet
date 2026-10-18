# Tensor frontend

Tensor's local meeting workspace, built with Angular 21, standalone components, signals, and the existing FastAPI/WebSocket backend.

## Run

From `tensor/`, run `start-tensor.cmd` to build and serve the complete app at http://localhost:8000.

For frontend development, run `npm.cmd start` here and start the backend separately on port 8000. The development server at http://localhost:4200 proxies `/api`, `/ws`, and `/kb` to the backend.

## Interface

- A join screen with name and meeting ID validation. Shared meeting links retain their meeting ID through the join screen. Microphones start off.
- Meeting room with Tensor's animated state, participant tiles, microphone and voice controls, and recent transcript.
- Full transcript, action center, and shared-file views accessible from the workspace navigation.
- Persistent meeting chat with Markdown, source links, native full-answer dialogs, prompt suggestions, and PDF sharing by file picker or drag and drop (15 MB limit).
- Editable Jira and email review cards. External execution still requires explicit human approval; sandbox labels reflect the backend executor.
- Responsive layouts, light and dark themes, keyboard focus states, native modal focus handling, and reduced-motion support.

## Source

- `src/app/join.ts`: join screen.
- `src/app/meeting-room.ts` and `.html`: meeting workspace and view state.
- `src/app/action-card.ts`: human review UI.
- `src/app/room.service.ts`: live state, WebSocket lifecycle, audio, and REST requests.
- `src/app/wake-word.ts`: fast browser wake hints matching the backend's punctuation and name variants.
- `src/app/icon.ts`: shared SVG icon set.
- `src/app/dialog.ts`: native modal wrapper.
- `src/styles.css`: design tokens, components, themes, animation, and responsive layouts.

## Check

```text
npm.cmd run build
npm.cmd run check:room
```

The build validates Angular templates and TypeScript and writes to `dist/tensor-ui/browser`. The room check exercises joining, snapshot sync, stale connection events, reconnect cleanup, and chat delivery using a local WebSocket stub. It uses no cloud services.
