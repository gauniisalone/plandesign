// Fenêtre « Se connecter » de plandesign.fr (B1d-b). Copiée dans dist/ seulement avec
// l'option de build PUBLIC_LOGIN_WINDOW=1 (scripts/login-assets.mjs). Fichier externe : la
// CSP du site n'autorise aucun script en ligne ; connect-src doit permettre l'API de l'app.
//
// Deux temps : l'adresse (POST /api/auth/email/start), puis le code (POST
// /api/auth/email/verify, avec les cookies : la session pd_session est posée par l'app, même
// site que plandesign.fr). Code juste : on part vers l'app, sur /nouveau (contrat v1) si le
// formulaire de l'accueil est rempli, sinon sur son accueil. Déjà connecté : pas de fenêtre,
// on part tout de suite vers l'app (GET /api/auth/me, statut seulement).
//
// KAN-104 : si /api/health dit `password_login`, une seconde voie, le mot de passe (POST
// /api/auth/password/login, avec les cookies). Fermé, ou sans réponse : rien n'en paraît.
(function () {
  "use strict";
  var dialog = document.getElementById("connexion");
  if (!dialog || typeof dialog.showModal !== "function") return;
  var app = dialog.getAttribute("data-app-origin") || "";

  // Bêta sur invitation (PO, 19 oct.) : le mode d'inscription est lu sur /api/health de l'app
  // (public, sans cookies). En mode « invite », les mentions [data-signup-invite] s'affichent
  // à la place de [data-signup-open] ; ouvert, ou sans réponse, rien ne change.
  fetch(app + "/api/health", { credentials: "omit" })
    .then(function (response) {
      return response.ok ? response.json() : {};
    })
    .then(function (health) {
      if (!health) return;
      // KAN-104 : vrai seulement, jamais par défaut.
      show("[data-password]", "[data-password-off]", health.password_login === true);
      show("[data-signup-invite]", "[data-signup-open]", health.signup === "invite");
    })
    .catch(function () {});
  function show(on, off, yes) {
    if (!yes) return;
    document.querySelectorAll(on).forEach(function (el) {
      el.hidden = false;
    });
    document.querySelectorAll(off).forEach(function (el) {
      el.hidden = true;
    });
  }
  var emailForm = dialog.querySelector('[data-step="email"]');
  var codeForm = dialog.querySelector('[data-step="code"]');
  var passwordForm = dialog.querySelector('[data-step="password"]');
  var emailInput = document.getElementById("connexion-email");
  var codeInput = document.getElementById("connexion-code");
  var pwEmailInput = document.getElementById("connexion-pw-email");
  var passwordInput = document.getElementById("connexion-password");
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
    passwordForm.hidden = name !== "password";
    say(emailForm, "");
    say(codeForm, "");
    say(passwordForm, "");
    if (name === "password") {
      // L'adresse déjà tapée suit d'un écran à l'autre.
      if (!pwEmailInput.value) pwEmailInput.value = emailInput.value;
      (pwEmailInput.value ? passwordInput : pwEmailInput).focus();
      return;
    }
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

  // Déjà connecté (session pd_session de l'app, même site) ? On va droit à l'app, sans
  // redemander de code. Seul le statut de GET /api/auth/me est lu (200 ou non) : rien de la
  // réponse (adresse, nom) n'est lu ni gardé par le site. 401 ou panne : la fenêtre, comme avant.
  var checking = false;
  function signedIn() {
    return fetch(app + "/api/auth/me", { credentials: "include", cache: "no-store" }).then(
      function (response) {
        return response.status === 200;
      },
      function () {
        return false;
      }
    );
  }

  document.addEventListener("click", function (event) {
    var opener = event.target.closest('[data-action="login"]');
    if (!opener) return;
    event.preventDefault();
    if (checking) return;
    checking = true;
    signedIn().then(function (yes) {
      checking = false;
      if (yes) {
        window.location.assign(target());
        return;
      }
      if (!dialog.open) dialog.showModal();
      step(email ? "code" : "email");
    });
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

  passwordForm.addEventListener("submit", function (event) {
    event.preventDefault();
    busy(passwordForm, true);
    post("/api/auth/password/login", {
      email: pwEmailInput.value.trim(),
      password: passwordInput.value,
    })
      .then(function (response) {
        if (response.ok) {
          window.location.assign(target());
          return;
        }
        busy(passwordForm, false);
        passwordInput.value = "";
        // Le texte du serveur, tel quel : refus unique (401), limite d'une heure (429).
        if (response.status === 401 || response.status === 429 || response.status === 503) {
          return detail(response).then(function (text) {
            say(passwordForm, text || DOWN);
          });
        }
        say(passwordForm, DOWN);
      })
      .catch(function () {
        busy(passwordForm, false);
        say(passwordForm, DOWN);
      });
  });

  dialog.querySelectorAll("[data-go]").forEach(function (button) {
    button.addEventListener("click", function () {
      var to = button.getAttribute("data-go");
      if (to === "email") email = "";
      step(to);
    });
  });
})();
