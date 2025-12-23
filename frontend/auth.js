// ===== CONFIG (DO NOT CHANGE unless Cognito changes) =====
const COGNITO_DOMAIN =
  "https://ap-south-1b5pqwc6sk.auth.ap-south-1.amazoncognito.com";

const CLIENT_ID = "15bs5m37s3oheg2o9ompiaoff9";

const REDIRECT_URI =
  "https://dg8837j63ledy.cloudfront.net/callback.html";

const LOGOUT_REDIRECT =
  "https://dg8837j63ledy.cloudfront.net/login.html";

// ===== LOGIN =====
function login() {
  const url =
    `${COGNITO_DOMAIN}/login?` +
    `response_type=token&` +
    `client_id=${CLIENT_ID}&` +
    `redirect_uri=${encodeURIComponent(REDIRECT_URI)}`;

  window.location.href = url;
}

// ===== CALLBACK =====
function handleCallback() {
  if (!window.location.hash) {
    document.body.innerHTML = "No token received";
    return;
  }

  const params = new URLSearchParams(window.location.hash.substring(1));

  const idToken = params.get("id_token");
  const accessToken = params.get("access_token");

  if (!idToken || !accessToken) {
    document.body.innerHTML = "Login failed";
    return;
  }

  sessionStorage.setItem("idToken", idToken);
  sessionStorage.setItem("accessToken", accessToken);

  // Redirect to app
  window.location.replace("/app.html");
}

// ===== LOGOUT =====
function logout() {
  sessionStorage.clear();

  const url =
    `${COGNITO_DOMAIN}/logout?` +
    `client_id=${CLIENT_ID}&` +
    `logout_uri=${encodeURIComponent(LOGOUT_REDIRECT)}`;

  window.location.href = url;
}

// ===== TOKEN HELPERS =====
function getIdToken() {
  return sessionStorage.getItem("idToken");
}