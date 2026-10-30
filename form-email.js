(function () {
  const oldForm = document.getElementById('request-form');
  if (!oldForm) return;
  const form = oldForm.cloneNode(true);
  oldForm.replaceWith(form);
  form.removeAttribute('onsubmit');
  form.method = 'POST';
  form.action = 'https://api.web3forms.com/submit';
  form.setAttribute('accept-charset', 'UTF-8');
  for (const [name, value] of [
    ['access_key', '18f9827c-8452-4537-83f3-a2fde5a44435'],
    ['subject', 'Новая заявка — HUSTLE LABEL MUSIC'],
    ['redirect', 'https://hustlelabel.ru/thanks.html']
  ]) {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = name;
    input.value = value;
    form.append(input);
  }
  // Web3Forms anti-bot honeypot.
  const botcheck = document.createElement('input');
  botcheck.type = 'checkbox';
  botcheck.name = 'botcheck';
  botcheck.style.display = 'none';
  botcheck.tabIndex = -1;
  botcheck.setAttribute('aria-hidden', 'true');
  form.append(botcheck);
  // Prevent any legacy navigation and submit directly to the Web3Forms API.
  form.addEventListener('submit', async function(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!form.reportValidity()) return;
    let status = form.querySelector('#submission-status');
    if (!status) {
      status = document.createElement('p');
      status.id = 'submission-status';
      status.setAttribute('role', 'status');
      form.append(status);
    }
    const button = form.querySelector('[type="submit"]');
    if (button) button.disabled = true;
    status.textContent = 'Отправляем заявку…';
    try {
      const response = await fetch('https://api.web3forms.com/submit', {
        method: 'POST', body: new FormData(form),
        headers: { Accept: 'application/json' }
      });
      const result = await response.json();
      if (!response.ok || !result.success) throw new Error(result.message || 'Заявка не принята');
      status.textContent = 'Заявка успешно отправлена!';
      status.style.color = '#128340';
      form.reset();
    } catch (error) {
      status.textContent = 'Не удалось отправить: ' + (error.message || 'ошибка сети');
      status.style.color = '#cc3434';
    } finally {
      if (button) button.disabled = false;
    }
  }, true);
  const btn = form.querySelector('[type="submit"]');
  if (btn) btn.textContent = 'ОТПРАВИТЬ ЗАЯВКУ ↗';
  const formHint = form.parentElement.querySelector('.hint');
  if (formHint) formHint.textContent = 'Заполни поля и отправь заявку напрямую команде лейбла.';
  const info = document.querySelector('#contact p[style*="margin-top:35px"]');
  if (info) info.textContent = 'Заявка отправляется через защищённую форму на электронную почту команды лейбла.';
  const intro = document.querySelector('#contact h2 + p');
  if (intro) intro.textContent = 'Хочешь выпустить музыку, подготовить релиз к продвижению или заказать оформление? Заполни заявку — мы получим её по электронной почте.';
  const output = document.getElementById('form-output');
  if (output) output.remove();
})();