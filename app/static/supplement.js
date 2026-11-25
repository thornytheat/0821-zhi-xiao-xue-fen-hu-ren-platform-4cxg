import {element} from './client.js';
import {badge} from './view.js';

const form = document.querySelector('#supplement-form');
const appInput = document.querySelector('#sup-app');
const receiptInput = document.querySelector('#sup-receipt');
const profile = document.querySelector('#sup-profile');
const fileInput = document.querySelector('#sup-file');
const notice = document.querySelector('#sup-notice');
const resultBox = document.querySelector('#sup-result');
const historyBox = document.querySelector('#sup-history');
const claimsBox = document.querySelector('#sup-claims');
const refreshButton = document.querySelector('#sup-refresh');

const reasonText = {
  COURSE_CLAIMED: '撞上其他批次已抵扣的课程，需按最新占用重新核算/换组配对',
  CREDIT_SHORTFALL: '换组后仍达不到本批最低学分',
  RECEIPT_CONFLICT: '同一回执编号提交了不同内容',
};

function appId() {
  return (appInput.value || '').trim() || '0001';
}

function say(text, failure = false) {
  notice.textContent = text;
  notice.dataset.failure = String(failure);
}

function pairLine(pair) {
  return `外校课 ${pair.external_course} → 校内课 ${pair.local_course}（${pair.credits} 学分）`;
}

function renderResult(outcome) {
  resultBox.replaceChildren();
  if (!outcome) {
    resultBox.textContent = '尚未提交补录批次。';
    return;
  }
  const head = element('div', '', {class: 'sup-head'});
  head.append(badge(outcome.status || outcome.conclusion));
  const title = element('strong', outcome.id ? `批次 ${String(outcome.id).slice(0, 12)}` : '核算结论');
  head.append(title);
  if (outcome.replayed) head.append(element('span', '回执重试 · 未重复计学分', {class: 'sup-replay'}));
  resultBox.append(head);

  const reason = outcome.reason ? (reasonText[String(outcome.reason)] || String(outcome.reason)) : '符合本批最低学分要求';
  resultBox.append(element('p', `原因：${reason}`, {class: 'sup-reason'}));
  resultBox.append(element('p',
    `本批认定 ${outcome.credits ?? outcome.value ?? 0} 学分 / 最低 ${outcome.expected ?? '—'} 学分` +
    (outcome.switched ? ' · 已换组配对' : '') +
    (outcome.collided ? ' · 原方案有撞课' : '')));

  const pairs = outcome.pairs || [];
  if (pairs.length) {
    const list = element('ul', '', {class: 'sup-pairs'});
    for (const pair of pairs) list.append(element('li', pairLine(pair)));
    resultBox.append(list);
  }
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.message || `请求失败 · ${response.status}`);
    error.payload = payload;
    throw error;
  }
  return payload;
}

async function submit(event) {
  event.preventDefault();
  const data = new FormData();
  data.append('application_id', appId());
  if (receiptInput.value.trim()) data.append('receipt', receiptInput.value.trim());
  if (fileInput.files.length) {
    data.append('file', fileInput.files[0]);
  } else if (profile.value) {
    data.append('sample', profile.value);
  }
  say('正在核算并签发…');
  try {
    const outcome = await fetchJson(`/api/applications/${encodeURIComponent(appId())}/supplements`,
      {method: 'POST', body: data});
    renderResult(outcome);
    const good = String(outcome.status) === 'ISSUED';
    say(good ? '补录批次已签发，配对已锁定' : `本批未签发：${outcome.reason || outcome.status}`, !good);
    await refresh();
  } catch (error) {
    if (error.payload && (error.payload.conclusion || error.payload.code)) {
      renderResult(error.payload);
    }
    say(error.message, true);
    await refresh();
  }
}

function renderHistory(summary) {
  historyBox.replaceChildren();
  const items = summary.items || summary.supplements || [];
  if (!items.length) {
    historyBox.append(element('li', '该申请暂无补录批次'));
  }
  for (const item of items) {
    const li = element('li', '', {class: 'sup-item'});
    const line = element('div', '', {class: 'sup-item-head'});
    line.append(badge(item.status || item.conclusion));
    line.append(element('code', String(item.id || '').slice(0, 12)));
    line.append(element('span', `${item.credits ?? 0} 学分`));
    li.append(line);
    const reason = item.reason ? (reasonText[String(item.reason)] || String(item.reason)) : '达到本批最低学分';
    li.append(element('div', reason, {class: 'sup-reason'}));
    if (item.receipt) li.append(element('div', `回执 ${item.receipt}`, {class: 'sup-muted'}));
    historyBox.append(li);
  }
  const claims = summary.claims || [];
  claimsBox.textContent = claims.length
    ? claims.map(c => `外校${c.external_course}→校内${c.local_course}（${c.credits}）`).join('；')
    : '—';
}

async function refresh() {
  try {
    const summary = await fetchJson(`/api/applications/${encodeURIComponent(appId())}`);
    renderHistory(summary);
  } catch (error) {
    historyBox.textContent = '暂无可查询的申请';
    claimsBox.textContent = '—';
  }
}

form.addEventListener('submit', submit);
refreshButton.addEventListener('click', refresh);
appInput.addEventListener('change', refresh);
fileInput.addEventListener('change', () => { if (fileInput.files.length) profile.value = ''; });
profile.addEventListener('change', () => { if (profile.value) fileInput.value = ''; });
document.addEventListener('supplement:refresh', refresh);
refresh();
