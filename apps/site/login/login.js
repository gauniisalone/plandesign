// Fenêtre « Se connecter » de plandesign.fr (B1d-b). Copiée dans dist/ seulement avec
// l'option de build PUBLIC_LOGIN_WINDOW=1 (scripts/login-assets.mjs). Fichier externe : la
// CSP du site n'autorise aucun script en ligne ; connect-src doit permettre l'API de l'app.
//
// Deux temps : l'adresse (POST /api/auth/email/start), puis le code (POST
// /api/auth/email/verify, avec les cookies : la session pd_session est posée par l'app, même
// site que plandesign.fr). Code juste : on part vers l'app, sur /nouveau (contrat v1) si le
// formulaire de l'accueil est rempli, sinon sur son accueil.
(function () {
  "use strict";
  var dialog = document.getElementById("connexion");
  if (!dialog || typeof dialog.showModal !== "function") return;
  var app = dialog.getAttribute("data-app-origin") || "";
  var emailForm = dialog.querySelector('[data-step="email"]');
  var codeForm = dialog.querySelector('[data-step="code"]');
  var emailInput = document.getElementById("connexion-email");
  var codeInput = document.getElementById("connexion-code");
  var email = "";

  function errorOf(form) {
    return form.querySelector(".login-error");
  }
  function say(form, text) {
    var p = errorOf(form);
    p.textContent = text || "";
    p.hidden = !text;
  }
  function busy(form, on) {
    var button = form.querySelector('button[type="submit"]');
    button.disabled = on;
  }
  function step(name) {
    emailForm.hidden = name !== "email";
    codeForm.hidden = name !== "code";
    say(emailForm, "");
    say(codeForm, "");
    (name === "email" ? emailInput : codeInput).focus();
  }
  function post(path, body) {
    return fetch(app + path, {
      method: "POST",
      credentials: "include",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  }
  function detail(response) {
    return response
      .json()
      .then(function (b) {
        return typeof b.detail === "string" ? b.detail : "";
      })
      .catch(function () {
        return "";
      });
  }
  function value(id) {
    var el = document.getElementById(id);
    return el ? String(el.value || "").trim() : "";
  }
  // Contrat /nouveau v1 (README) : description obligatoire, champs vides envoyés vides.
  function target() {
    var description = value("description");
    if (!description) return app + "/";
    var q = new URLSearchParams();
    q.set("v", "1");
    q.set("source", "site");
    q.set("piece", value("piece"));
    q.set("surface", value("surface"));
    q.set("description", description);
    return app + "/nouveau?" + q.toString();
  }
  var DOWN = "Le service ne répond pas : réessayez dans un instant.";
  var SLOW = "Trop de tentatives : réessayez dans une minute.";

  document.addEventListener("click", function (event) {
    var opener = event.target.closest('[data-action="login"]');
    if (!opener) return;
    event.preventDefault();
    if (!dialog.open) dialog.showModal();
    step(email ? "code" : "email");
  });

  emailForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var typed = emailInput.value.trim();
    busy(emailForm, true);
    post("/api/auth/email/start", { email: typed })
      .then(function (response) {
        if (response.ok) {
          email = typed;
          codeInput.value = "";
          step("code");
          return;
        }
        if (response.status === 422) say(emailForm, "Cette adresse e-mail n'est pas valable.");
        else if (response.status === 429) say(emailForm, SLOW);
        else say(emailForm, DOWN);
      })
      .catch(function () {
        say(emailForm, DOWN);
      })
      .then(function () {
        busy(emailForm, false);
      });
  });

  codeForm.addEventListener("submit", function (event) {
    event.preventDefault();
    busy(codeForm, true);
    post("/api/auth/email/verify", { email: email, code: codeInput.value })
      .then(function (response) {
        if (response.ok) {
          window.location.assign(target());
          return;
        }
        busy(codeForm, false);
        if (response.status === 429) return say(codeForm, SLOW);
        return detail(response).then(function (text) {
          say(codeForm, "Connexion impossible : " + (text || "ce code n'est pas bon, ou il a expiré."));
        });
      })
      .catch(function () {
        busy(codeForm, false);
        say(codeForm, DOWN);
      });
  });

  dialog.querySelector(".login-back").addEventListener("click", function () {
    email = "";
    step("email");
  });
})();
