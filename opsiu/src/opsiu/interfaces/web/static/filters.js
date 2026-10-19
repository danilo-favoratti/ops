/* Local filter controls. The native selects remain the submitted source of truth. */
(() => {
  const normalized = value => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("pt-BR");
  document.querySelectorAll("[data-deputy-picker]").forEach(select => {
    const options = Array.from(select.options);
    const wrapper = document.createElement("div");
    wrapper.className = "deputy-picker";
    const tags = document.createElement("div");
    tags.className = "selected-deputies";
    tags.setAttribute("aria-label", "Parlamentares selecionados");
    const input = document.createElement("input");
    input.type = "search";
    input.id = select.id;
    select.id += "-values";
    input.placeholder = "Digite nome ou código";
    input.autocomplete = "off";
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-describedby", "deputy-help");
    const results = document.createElement("div");
    results.id = input.id + "-results";
    results.className = "deputy-results";
    results.setAttribute("role", "listbox");
    results.setAttribute("aria-label", "Parlamentares encontrados");
    input.setAttribute("aria-controls", results.id);
    const status = document.createElement("span");
    status.className = "small muted";
    status.setAttribute("role", "status");
    results.hidden = true;
    wrapper.append(input, results, tags, status);
    select.after(wrapper);
    select.hidden = true;

    const close = () => {
      results.hidden = true;
      input.setAttribute("aria-expanded", "false");
    };
    const updateTags = () => {
      tags.replaceChildren();
      options.filter(option => option.selected).forEach(option => {
        const tag = document.createElement("button");
        tag.type = "button";
        tag.className = "deputy-tag";
        tag.textContent = option.textContent + " ×";
        tag.setAttribute("aria-label", "Remover " + option.textContent);
        tag.addEventListener("click", () => {
          option.selected = false;
          updateTags();
          input.focus();
          search();
        });
        tags.append(tag);
      });
    };
    const search = () => {
      const query = normalized(input.value.trim());
      const matches = options.filter(option => !option.selected && normalized(option.textContent).includes(query));
      results.replaceChildren();
      matches.slice(0, 20).forEach(option => {
        const button = document.createElement("button");
        button.type = "button";
        button.setAttribute("role", "option");
        button.setAttribute("aria-selected", "false");
        button.textContent = option.textContent;
        button.addEventListener("click", () => {
          option.selected = true;
          updateTags();
          input.value = "";
          input.focus();
          close();
          status.textContent = "Parlamentar adicionado.";
        });
        results.append(button);
      });
      status.textContent = matches.length === 0 ? "Nenhum parlamentar encontrado." :
        matches.length > 20 ? "Mostrando 20 resultados. Digite mais para refinar." : `${matches.length} resultado(s).`;
      results.hidden = matches.length === 0;
      input.setAttribute("aria-expanded", String(matches.length > 0));
    };
    input.addEventListener("input", search);
    input.addEventListener("focus", search);
    input.addEventListener("keydown", event => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        if (results.hidden) search();
        results.firstElementChild?.focus();
      }
      if (event.key === "Enter" && !results.hidden) {
        event.preventDefault();
        results.firstElementChild?.click();
      }
      if (event.key === "Escape") close();
    });
    results.addEventListener("keydown", event => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const next = event.key === "ArrowDown" ? event.target.nextElementSibling : event.target.previousElementSibling;
        if (next) next.focus(); else input.focus();
      }
      if (event.key === "Escape") { input.focus(); close(); }
    });
    wrapper.addEventListener("focusout", event => {
      if (!wrapper.contains(event.relatedTarget)) close();
    });
    document.addEventListener("click", event => {
      if (!wrapper.contains(event.target)) close();
    });
    updateTags();
  });
  document.querySelectorAll(".uf-picker").forEach(picker => {
    picker.addEventListener("change", () => {
      const selected = Array.from(picker.querySelectorAll("input:checked"), input => input.value === "NA" ? "Sem UF" : input.value);
      picker.querySelector("summary span").textContent = selected.length ? selected.join(", ") : "Todas";
    });
  });
})();
