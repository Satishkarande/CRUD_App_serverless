# FlowTasks Neon Frontend

This is a replacement static frontend for the existing FlowTasks AWS serverless backend.

## Files
- `app.html` — application shell and views
- `assets/app.css` — neon-carbon dashboard styling
- `assets/app.js` — API integration and UI behavior
- `login.html`, `callback.html`, `auth.js` — existing Cognito PKCE login flow

## Run locally
From this folder:
```powershell
python -m http.server 8000
```
Open `http://localhost:8000/login.html`.

## API
Uses `https://rq067no9cb.execute-api.ap-south-1.amazonaws.com` and the existing Cognito access token.

## Backend compatibility notes
- The deployed TaskUpdate handler only accepts `status` and `priority`; title, description and category editing are not available through the current API.
- The uploaded `lambda-packages/DeleteComment/lambda_function.py` appears to contain the GetUsers handler rather than a comment-delete handler. The UI calls the existing `DELETE /tasks/{taskId}/comments/{commentId}` route, but successful deletion requires that Lambda integration to be corrected and deployed.
- AddComment's uploaded package reads `pathParameters.id`, while the route script defines `{taskId}`. Verify the deployed package accepts the route's parameter name. The current UI uses the documented route.
- The app does not claim a feature is saved unless the API request succeeds.
