(function () {
  const oldForm = document.getElementById('request-form');
  if (!oldForm) return;
  const form = oldForm.cloneNode(true);
  oldForm.replaceWith(form);
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