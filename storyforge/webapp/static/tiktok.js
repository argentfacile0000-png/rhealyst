// Écran de publication TikTok : options de divulgation, visibilité « Moi seul » interdite pour un contenu
// de marque, boutons actifs seulement une fois la visibilité choisie (règles de publication de TikTok).
(() => {
  const f = document.getElementById("tt-form");
  if (!f) return;
  const { disclose, brand_organic: organic, brand_content: branded, privacy } = f.elements;
  const box = document.getElementById("brand-options");
  const hint = document.getElementById("label-hint");
  const policy = document.getElementById("bc-policy");
  const buttons = f.querySelectorAll("[data-needs-choice]");
  function update() {
    box.hidden = !disclose.checked;
    if (!disclose.checked) { organic.checked = false; branded.checked = false; }
    const selfOnly = privacy.querySelector('option[value="SELF_ONLY"]');
    if (selfOnly) {
      selfOnly.disabled = branded.checked;
      if (branded.checked && privacy.value === "SELF_ONLY") privacy.value = "";
    }
    hint.textContent = branded.checked ? "Tes vidéos seront étiquetées « Partenariat rémunéré »."
      : organic.checked ? "Tes vidéos seront étiquetées « Contenu promotionnel »." : "";
    policy.hidden = !branded.checked;
    const ok = Boolean(privacy.value) && (!disclose.checked || organic.checked || branded.checked);
    buttons.forEach((b) => { b.disabled = !ok; });
  }
  f.addEventListener("change", update);
  update();
})();
