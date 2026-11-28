(() => {
  const parent = document.getElementById('connectionSettings');
  if (!parent || document.getElementById('serviceCredentials')) return;
  const section = document.createElement('section');
  section.id = 'serviceCredentials';
  section.className = 'processingConnection';
  section.setAttribute('aria-labelledby', 'serviceCredentialsTitle');
  section.innerHTML = `
    <h3 id="serviceCredentialsTitle">Notion 与 API 服务凭据</h3>
    <p class="fieldHelp">保存在运行工作台的电脑上，供这台工作台的所有项目共用。更换凭据会影响使用它的 API 来源、日报或稿件接口，以及旧版 Notion 来源和自动快照。项目资料库和审核稿归档使用「采集管理」中各连接独立保存的授权。页面只显示是否已保存，不会显示密钥。</p>
    <details class="operatorDetails"><summary>旧版 Notion 来源与自动快照（兼容）</summary><form id="notionCredentialForm">
      <h4>共用 Notion 集成</h4>
      <p class="fieldHelp">在 <a href="https://www.notion.so/profile/integrations" target="_blank" rel="noopener noreferrer">Notion 集成管理</a> 创建或管理集成，复制其密钥并保存。随后在 Notion 中打开要使用的资料库，通过「连接」将资料库共享给该集成，再到「模型与多媒体服务 → 更多连接检测」检查读取权限。新建素材资料库请到「采集管理 → 按渠道采集 → 资料库」配置。保存密钥本身不会验证连接。</p>
      <label for="notionServiceToken">Notion 集成密钥<input id="notionServiceToken" name="token" type="password" autocomplete="new-password" maxlength="8192" aria-describedby="notionCredentialHelp notionCredentialStatus"></label>
      <p id="notionCredentialHelp" class="fieldHelp">留空保留已保存的密钥；输入新密钥会替换原密钥。</p>
      <p id="notionCredentialStatus" role="status" aria-live="polite">正在读取保存状态…</p>
      <button type="submit" class="secondary">保存 Notion 密钥</button>
    </form></details>
    <details class="operatorDetails">
      <summary>命名 API 凭据（选填）</summary>
      <p class="fieldHelp">需要 Bearer 密钥的 API 采集源或第三方接口可共用命名凭据。保存后，在对应来源的凭据名称栏选择这个名称。公开 RSS 或无需鉴权的 API 可留空。</p>
      <form id="apiCredentialForm">
        <label for="serviceApiAlias">凭据名称<input id="serviceApiAlias" name="alias" required maxlength="100" pattern="DM_API_TOKEN_[A-Z0-9_]+" list="savedApiCredentials" placeholder="例如：DM_API_TOKEN_MAIN" aria-describedby="apiCredentialHelp"></label>
        <label for="serviceApiToken">API 密钥<input id="serviceApiToken" name="token" type="password" autocomplete="new-password" maxlength="8192" aria-describedby="apiCredentialHelp apiCredentialStatus"></label>
        <p id="apiCredentialHelp" class="fieldHelp">名称以 DM_API_TOKEN_ 开头，后面使用大写字母、数字或下划线。留空保留该名称已保存的密钥；填写新密钥会替换它。</p>
        <p id="apiCredentialStatus" role="status" aria-live="polite">正在读取已保存的凭据名称…</p>
        <button type="submit" class="secondary">保存 API 凭据</button>
      </form>
    </details>
    <datalist id="savedApiCredentials"></datalist>
    <p id="serviceCredentialsStatus" role="status" aria-live="polite"></p>`;
  const matrix = document.getElementById('envMatrix');
  if (matrix && matrix.parentNode === parent) parent.insertBefore(section, matrix);
  else parent.appendChild(section);

  const notionForm = section.querySelector('#notionCredentialForm');
  const apiForm = section.querySelector('#apiCredentialForm');
  const notionStatus = section.querySelector('#notionCredentialStatus');
  const apiStatus = section.querySelector('#apiCredentialStatus');
  apiStatus.style.overflowWrap = 'anywhere';
  const status = section.querySelector('#serviceCredentialsStatus');
  const list = section.querySelector('#savedApiCredentials');
  const buttons = section.querySelectorAll('button[type="submit"]');
  let busy = false;
  let revision = 0;

  function attachAliases() {
    document.querySelectorAll('[name="token_alias"]').forEach(input => {
      input.setAttribute('list', 'savedApiCredentials');
    });
  }

  function render(metadata) {
    // Consume only metadata. Never populate a password input from a response.
    if (typeof metadata.notion_saved !== 'boolean' || !Array.isArray(metadata.api_aliases)) {
      throw new Error('Invalid credential metadata');
    }
    const aliases = metadata.api_aliases.filter(alias =>
      typeof alias === 'string' && alias.length <= 100 && /^DM_API_TOKEN_[A-Z0-9_]+$/.test(alias));
    notionStatus.textContent = metadata.notion_saved ? 'Notion 密钥已保存。留空可保留。' : '尚未保存 Notion 密钥。';
    notionForm.elements.token.placeholder = metadata.notion_saved ? '已保存；留空保留' : '粘贴 Notion 集成密钥';
    apiStatus.textContent = aliases.length ? `已保存：${aliases.join('、')}` : '尚未保存命名 API 凭据。';
    list.replaceChildren(...aliases.map(alias => {
      const option = document.createElement('option');
      option.value = alias;
      return option;
    }));
    attachAliases();
  }

  async function request(path, body) {
    const options = {cache: 'no-store', credentials: 'same-origin'};
    if (body !== undefined) {
      options.method = 'POST';
      options.headers = {'Content-Type': 'application/json'};
      options.body = JSON.stringify(body);
    }
    const response = await fetch('/api/service-credentials' + path, options);
    // Do not surface server/proxy error bodies: they could contain submitted data.
    if (!response.ok) throw new Error('Credential request failed');
    return response.json();
  }

  window.refreshServiceCredentials = async () => {
    attachAliases();
    if (busy) return;
    const current = ++revision;
    try {
      const metadata = await request('');
      if (current === revision) {
        render(metadata);
        status.textContent = '';
      }
    } catch {
      if (current === revision) status.textContent = '凭据状态暂时无法读取。请重新打开设置或刷新页面重试。';
    }
  };

  function bind(form, path) {
    form.addEventListener('submit', async event => {
      event.preventDefault();
      event.stopPropagation();
      if (busy) return;
      const token = form.elements.token.value;
      const body = {token};
      if (path === '/api') body.alias = form.elements.alias.value.trim();
      busy = true;
      ++revision;
      buttons.forEach(button => { button.disabled = true; });
      section.setAttribute('aria-busy', 'true');
      status.textContent = '正在保存凭据…';
      try {
        const metadata = await request(path, body);
        form.elements.token.value = '';
        render(metadata);
        status.textContent = token.trim()
          ? '凭据已保存，密钥输入框已清空。尚未检测服务连接。'
          : '未更改密钥，已保留原有设置。';
      } catch {
        status.textContent = '凭据未能确认保存。请检查名称和密钥后重试，或刷新页面查看保存状态。';
      } finally {
        busy = false;
        buttons.forEach(button => { button.disabled = false; });
        section.setAttribute('aria-busy', 'false');
      }
    });
  }

  bind(notionForm, '/notion');
  bind(apiForm, '/api');
  void window.refreshServiceCredentials();
})();
