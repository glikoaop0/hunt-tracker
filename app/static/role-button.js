document.addEventListener("keyup", (event) => {
    if (event.key !== "Enter") return;
    const target = event.target;
    if (target instanceof HTMLElement && target.matches('[role="button"][hx-get]')) target.click();
});
