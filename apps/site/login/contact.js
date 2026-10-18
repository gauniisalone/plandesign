// Formulaire de contact de plandesign.fr (copié dans dist/ seulement avec l'option de build
// PUBLIC_LOGIN_WINDOW=1). POST /api/contact sur l'API de l'app, sans cookies ; réponse neutre.
(function () {
  "use strict";
  var form = document.getElementById("contact");
  if (!form) return;
  var app = form.getAttribute("data-app-origin") || "";
  var status = form.querySelector(".contact-status");
  var button = form.querySelector('button[type="submit"]');
  function say(text, ok) {
    status.textContent = text;
    status.hidden = !text;
    status.className = "contact-status" + (ok ? " ok" : " error");
  }
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var email = form.elements.email.value.trim();
    var message = form.elements.message.value.trim();
    if (!email || !message) return say("Indiquez votre adresse et votre message.", false);
    if (!form.elements.consent.checked)
      return say("Cochez la case pour que nous puissions vous répondre.", false);
    button.disabled = true;
    fetch(app + "/api/contact", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        email: email,
        message: message,
        consent: true,
        website: form.elements.website.value,
      }),
    })
      .then(function (response) {
        return response
          .json()
          .catch(function () {
            return {};
          })
          .then(function (body) {
            if (response.ok) {
              form.reset();
              say(body.detail || "Merci : votre message est parti.", true);
            } else {
              say(
                typeof body.detail === "string" ? body.detail : "Envoi impossible : réessayez plus tard.",
                false,
              );
            }
          });
      })
      .catch(function () {
        say("Le service ne répond pas : réessayez dans un instant.", false);
      })
      .then(function () {
        button.disabled = false;
      });
  });
})();
