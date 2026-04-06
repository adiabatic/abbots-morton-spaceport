export function initGearMenu(toolbarSelector) {
  const toolbar = document.querySelector(toolbarSelector);
  const gearBtn = toolbar.querySelector(".toggle-buttons__gear");
  const panel = toolbar.querySelector(".toggle-buttons__panel");

  let hoverTimer = null;
  let unhoverTimer = null;
  let tempExpanded = false;

  function expand(temp) {
    clearTimeout(hoverTimer);
    clearTimeout(unhoverTimer);
    panel.classList.remove("collapsed");
    gearBtn.classList.add("gear-open");
    tempExpanded = temp;
  }

  function collapse() {
    clearTimeout(hoverTimer);
    clearTimeout(unhoverTimer);
    panel.classList.add("collapsed");
    gearBtn.classList.remove("gear-open");
    tempExpanded = false;
  }

  function isExpanded() {
    return !panel.classList.contains("collapsed");
  }

  gearBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (isExpanded() && !tempExpanded) {
      collapse();
    } else {
      expand(false);
    }
  });

  toolbar.addEventListener("mouseenter", () => {
    clearTimeout(unhoverTimer);
    if (!isExpanded()) {
      hoverTimer = setTimeout(() => expand(true), 400);
    }
  });

  toolbar.addEventListener("mouseleave", () => {
    clearTimeout(hoverTimer);
    if (tempExpanded) {
      unhoverTimer = setTimeout(collapse, 2500);
    }
  });

  document.addEventListener("click", (e) => {
    if (isExpanded() && !tempExpanded && !e.target.closest(toolbarSelector)) {
      collapse();
    }
  });
}
