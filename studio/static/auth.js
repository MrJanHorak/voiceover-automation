// Sign-in, sign-up and first-run setup. Requests carry the studio header, like every change.
const form = document.getElementById("auth-form");
const mode = document.body.dataset.mode;
const routes = { login: "/api/auth/login", signup: "/api/auth/signup", setup: "/api/auth/setup" };

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = document.getElementById("auth-error");
  const submit = document.getElementById("auth-submit");
  const value = (id) => document.getElementById(id)?.value ?? "";
  const body = { email: value("email"), password: value("password") };
  if (mode === "signup") Object.assign(body, { name: value("name"), terms: document.getElementById("terms").checked });
  if (mode === "setup") body.code = value("code");
  error.hidden = true;
  submit.disabled = true;
  try {
    const response = await fetch(routes[mode], {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Studio-Request": "1" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || "Something went wrong. Try again.");
    }
    window.location.replace("/");
  } catch (failure) {
    error.textContent = failure.message;
    error.hidden = false;
    submit.disabled = false;
  }
});
