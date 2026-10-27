// The page works without this file; it only makes two things quicker to use.

// Filters apply as soon as one changes. The Filter button is for browsers without JavaScript.
for (const form of document.querySelectorAll("form.filters")) {
  form.querySelector("button[type=submit]").hidden = true;
  form.addEventListener("change", () => form.requestSubmit());
}

// A button with data-busy says so while its form is being answered, and can't be pressed twice.
for (const button of document.querySelectorAll("button[data-busy]")) {
  button.form.addEventListener("submit", () => {
    button.disabled = true;
    button.textContent = button.dataset.busy;
  });
}

// Coming back with the browser's Back button can restore a page still saying "Checking…".
addEventListener("pageshow", (event) => event.persisted && location.reload());
