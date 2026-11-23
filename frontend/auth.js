
const COGNITO_DOMAIN =
  "https://ap-south-1stecbrdby.auth.ap-south-1.amazoncognito.com";

const CLIENT_ID = "2il8s79s03g1tqarsvphv0gsoe";

const REDIRECT_URI = "http://localhost:8000/callback.html";
const LOGOUT_REDIRECT = "http://localhost:8000/login.html";

const COGNITO_SCOPES = "openid email phone";

// Generate a cryptographically random PKCE verifier.
function generateRandomString(length = 64) {
  const charset =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";

  const randomValues = new Uint8Array(length);
  crypto.getRandomValues(randomValues);

  return Array.from(randomValues)
    .map(value => charset[value % charset.length])
    .join("");
}

// Convert a string to URL-safe Base64.
function base64UrlEncode(buffer) {
  return btoa(String.fromCharCode(...new Uint8Array(buffer)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

// Generate SHA-256 PKCE challenge.
async function generateCodeChallenge(verifier) {
  const data = new TextEncoder().encode(verifier);
  const digest = await crypto.subtle.digest("SHA-256", data);

  return base64UrlEncode(digest);
}

// LOGIN
async function login() {
  try {
    const verifier = generateRandomString(64);
    const challenge = await generateCodeChallenge(verifier);
    const state = generateRandomString(32);

    sessionStorage.setItem("pkce_verifier", verifier);
    sessionStorage.setItem("oauth_state", state);

    const params = new URLSearchParams({
      response_type: "code",
      client_id: CLIENT_ID,
      redirect_uri: REDIRECT_URI,
      scope: COGNITO_SCOPES,
      code_challenge_method: "S256",
      code_challenge: challenge,
      state: state,
      // Always show Cognito sign-in so a different account can be selected.
      prompt: "login"
    });

    window.location.href =
      `${COGNITO_DOMAIN}/oauth2/authorize?${params.toString()}`;

  } catch (error) {
    console.error("Login initialization failed:", error);
    alert("Unable to start login. Please try again.");
  }
}

// CALLBACK
async function handleCallback() {
  try {
    const params = new URLSearchParams(window.location.search);

    const error = params.get("error");
    if (error) {
      throw new Error(
        params.get("error_description") || error
      );
    }

    const code = params.get("code");
    const returnedState = params.get("state");

    const savedState = sessionStorage.getItem("oauth_state");
    const verifier = sessionStorage.getItem("pkce_verifier");

    if (!code || !returnedState || !savedState || !verifier) {
      throw new Error("Missing authorization code or PKCE data.");
    }

    if (returnedState !== savedState) {
      throw new Error("OAuth state validation failed.");
    }

    const tokenResponse = await fetch(
      `${COGNITO_DOMAIN}/oauth2/token`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded"
        },
        body: new URLSearchParams({
          grant_type: "authorization_code",
          client_id: CLIENT_ID,
          code: code,
          redirect_uri: REDIRECT_URI,
          code_verifier: verifier
        })
      }
    );

    const tokens = await tokenResponse.json();

    if (!tokenResponse.ok) {
      throw new Error(
        tokens.error_description || tokens.error || "Token exchange failed."
      );
    }

    if (!tokens.id_token || !tokens.access_token) {
      throw new Error("Cognito did not return the required tokens.");
    }

    localStorage.setItem("id_token", tokens.id_token);
    localStorage.setItem("access_token", tokens.access_token);

    sessionStorage.removeItem("pkce_verifier");
    sessionStorage.removeItem("oauth_state");

    window.location.replace("/app.html");

  } catch (error) {
    console.error("Authentication callback failed:", error);

    sessionStorage.removeItem("pkce_verifier");
    sessionStorage.removeItem("oauth_state");

    document.body.innerHTML = `
      <h2>Login failed</h2>
      <p>${error.message}</p>
      <a href="/login.html">Return to login</a>
    `;
  }
}

// LOGOUT
function cognitoLogout() {
  // Clear all FlowTasks authentication state before leaving the application.
  localStorage.removeItem("id_token");
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  sessionStorage.clear();

  const params = new URLSearchParams({
    client_id: CLIENT_ID,
    logout_uri: LOGOUT_REDIRECT
  });

  // Cognito must have LOGOUT_REDIRECT registered as an allowed sign-out URL.
  const cognitoLogoutUrl = `${COGNITO_DOMAIN}/logout?${params.toString()}`;
  try {
    window.location.assign(cognitoLogoutUrl);
  } catch (error) {
    console.error("Cognito logout redirect failed:", error);
    window.location.replace(LOGOUT_REDIRECT);
  }
}

// TOKEN HELPERS
function getIdToken() {
  return localStorage.getItem("id_token");
}

function getAccessToken() {
  return localStorage.getItem("access_token");
}
