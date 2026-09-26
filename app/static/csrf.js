document.addEventListener("htmx:configRequest", (event) => {
    if (event.detail.verb === "get") return;
    const token = document.querySelector('meta[name="csrf-token"]')?.content;
    if (token) event.detail.headers["X-CSRF-Token"] = token;
});
