const { chromium } = require("playwright");
const assert = require("node:assert/strict");

async function testLocalAccount(page, base) {
  const email = "quantumone-check-" + crypto.randomUUID() + "@example.invalid";
  const password = crypto.randomUUID();
  await page.goto(base + "/cadastro");
  await page.locator("#registration-name").fill("Teste de integração");
  await page.locator("#registration-email").fill(email);
  await page.locator("#registration-password").fill(password);
  await page.locator("#registration-confirmPassword").fill(password);
  await page.getByRole("button", { name: "Criar conta", exact: true }).click();
  await page.getByRole("heading", { name: "Meu perfil" }).waitFor();
  await page.getByRole("button", { name: "Enviar código de confirmação" }).click();
  const field = page.getByLabel("Código de confirmação de 6 dígitos");
  await field.waitFor();
  const mailbox = process.env.MAILPIT_URL || "http://localhost:8025";
  const list = await page.request.get(mailbox + "/api/v1/search", {params:{ query: "to:" + email }});
  const message = (await list.json()).messages[0];
  assert.ok(message, "Mailpit recebeu o código da conta de teste");
  const body = await (await page.request.get(mailbox + "/api/v1/message/" + message.ID)).json();
  const code = body.Text.match(/\b\d{6}\b/)[0];
  await field.fill(code);
  await page.getByRole("button", {name: "Confirmar e-mail", exact: true}).click();
  const nextPassword = crypto.randomUUID();
  await page.getByPlaceholder("Pelo menos 8 caracteres").fill(nextPassword);
  await page.getByPlaceholder("Digite a senha novamente").fill(nextPassword);
  await page.getByRole("button", {name:"Salvar nova senha"}).click();
  await page.getByText("Senha atualizada", {exact:true}).waitFor();
  await page.goto(base + "/cadastro");
  await page.getByRole("button", {name:/Já tenho conta/}).click();
  await page.locator("#registration-email").fill(email);
  await page.locator("#registration-password").fill(nextPassword);
  await page.getByRole("button", {name:"Entrar",exact:true}).click();
  await page.getByRole("heading", {name:"Meu perfil"}).waitFor();
  console.log("OK: cadastro, perfil, recebimento local do código, confirmação, nova senha e login.");
}

async function run() {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH });
  const page = await browser.newPage({ acceptDownloads: true });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  page.setDefaultTimeout(15000);
  const button = name => page.getByRole("button", { name, exact: true });
  const tab = name => page.getByRole("tab", { name, exact: true });
  const base = process.env.FRONTEND_URL || "http://localhost:3000";
  try {
    if (process.env.TEST_LOCAL_ACCOUNT === "1") await testLocalAccount(page, base);
    await page.goto(base);
    await page.locator(".startup-splash").waitFor({state:"detached"});
    await page.evaluate(() => {
      localStorage.removeItem("quantumone.conversations.v1");
      localStorage.setItem("quantumone.chat.v1.legacy", JSON.stringify({
        id: "legacy", title: "Conversa da versão anterior", updated: Date.now(), format: "qasm", code: "", topK: "3",
        turns: [{input:"Pergunta anterior", format:"qasm", code:"", result:{answer:"Resposta anterior", request_id:"old", warnings:[], sources:[], status:"ok"}}]
      }));
    });
    await page.reload();
    await button("Histórico").click();
    await page.getByText("Conversa da versão anterior", {exact:true}).first().waitFor();
    await button("Nova análise").first().click();
    await button("Analisar").click();
    await page.getByText("Preencha o código", { exact: false }).waitFor();
    async function circuit(language, code) {
      await button("Limpar").click();
      await button("Adicionar código").click();
      const dialog = page.getByRole("dialog");
      await dialog.getByRole("button", { name: language, exact: true }).click();
      await dialog.locator(".cm-content").fill(code);
      await dialog.getByRole("button", { name: "Adicionar código" }).click();
      await button("Analisar").click();
    }
    const header = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\n';
    await circuit("OpenQASM 2", header + "h q[0];\ncx q[0],q[1];");
    await page.getByRole("heading", { name: "Resultado", exact: true }).waitFor();
    await tab("Qiskit").click();
    await page.getByText("Circuitos equivalentes", { exact: true }).waitFor();
    await tab("Diff").click();
    await button("Aprovar este arquivo").click();
    await page.getByRole("dialog").getByText("otimizado.qasm", { exact: true }).waitFor();
    await page.getByRole("dialog").getByRole("button", { name: "Adicionar código" }).click();
    const download = page.waitForEvent("download");
    await button("Exportar JSON").click();
    assert.match((await download).suggestedFilename(), /\.json$/);
    await circuit("OpenQASM 2", header + "h q[12];");
    await page.getByText(/Linha 4\./).waitFor();
    const python = "from qiskit import QuantumCircuit\nqc=QuantumCircuit(2)\nqc.h(0)\nqc.cx(0,1)";
    await circuit("Python", python);
    await page.getByRole("heading", { name: "Resultado", exact: true }).waitFor();
    await circuit("Python", python + "\nqc.h(8)");
    await page.getByText(/Linha 5\./).waitFor();
    await circuit("Python", "from qiskit import QuantumCircuit\nfrom qiskit.circuit import Parameter");
    await page.getByText(/Parameter não é suportado/).waitFor();
    await button("Limpar").click();
    await tab("Terminal").click();
    await page.getByLabel("Comando do terminal").fill("limites");
    await button("Executar").click();
    await page.locator(".terminal-output-lines").getByText(/max_circuit_lines/).waitFor();
    await button("Projetos").click();
    await page.locator(".project-create-button").click();
    await page.getByPlaceholder("Ex.: Otimização de circuitos").fill("Teste de integração");
    await page.getByRole("dialog").locator("button[type=submit]").click();
    await page.getByRole("heading", { name: "Teste de integração" }).waitFor();
    await page.locator(".project-open-button").first().click();
    await page.getByText("PROJETO ATIVO", { exact: true }).waitFor();
    await page.getByRole("combobox", { name: "Fontes por consulta" }).selectOption("8");
    const turns = [];
    if (process.env.SKIP_LIVE_LLM === "1") {
      await page.route("**/api/conversations/*/messages", route => {
        const input = route.request().postDataJSON();
        assert.equal(input.top_k, 8);
        if (turns.length === 0 && input.web_search) {
          turns.push(route.request().url());
          return route.fulfill({ status: 503, json: { code: "WEB_SEARCH_UNAVAILABLE", message: "Busca web indisponível" } });
        }
        assert.equal(input.web_search, turns.length !== 1);
        assert.match(input.input, /Contexto da amostra/);
        turns.push(route.request().url());
        return route.fulfill({ json: { response: "Resposta de teste", status: "ok", sources: [
          { document_id: "manual", chunk_id: "1", source: "https://example.org", title: "Manual", content: "Trecho", origin: "local" }
        ], warnings: [], request_id: "teste" } });
      });
    }
    await page.locator('input[type=file]').first().setInputFiles({ name: "nota.txt", mimeType: "text/plain", buffer: Buffer.from("Contexto da amostra.") });
    await page.locator("#analysis-question").fill("Explique a transpilação no Qiskit.");
    await button("Analisar").click();
    await page.getByRole("heading", { name: "Resultado", exact: true }).waitFor({ timeout: 120000 });
    await tab("Fontes").click();
    await page.locator(".workspace-source-card").first().click();
    await page.getByRole("dialog").waitFor();
    await page.keyboard.press("Escape");
    await page.locator("#analysis-question").fill("Como a profundidade é medida?");
    await button("Analisar").click();
    await page.locator(".analysis-result-panel").nth(1).waitFor({ timeout: 120000 });
    if (turns.length) {
      assert.equal(turns.length, 3);
      assert.equal(new Set(turns).size, 1);
      await page.getByText(/Esta resposta usou apenas a documentação local/).waitFor();
    }
    await button("Histórico").click();
    await page.getByRole("heading", { name: "Histórico" }).waitFor();
    await page.reload();
    await button("Histórico").click();
    await page.getByText(/Explique a transpilação/).first().waitFor();
    await button("Configurações").click();
    await page.getByRole("heading", { name: "Configurações" }).waitFor();
    const backupEvent = page.waitForEvent("download");
    await button("Baixar cópia").click();
    const backup = await backupEvent;
    page.once("dialog", dialog => dialog.accept());
    await page.getByLabel("Selecionar cópia do workspace").setInputFiles(await backup.path());
    await page.getByText("Backup do workspace restaurado.", { exact: true }).first().waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(base);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.deepEqual(errors, []);
    console.log("OK: QASM, Python, diagnósticos, métricas, diff, exportação, limites, projetos, anexos, conversa, fontes, histórico, configurações e tela móvel.");
  } catch (error) {
    console.error("Conversations:", await page.evaluate(() => JSON.parse(localStorage.getItem("quantumone.conversations.v1") || "[]").map(item => item.title)));
    console.error((await page.locator(".form-feedback.error, .settings-data-note, .profile-feedback").allTextContents()).join("\n"));
    throw error;
  } finally { await browser.close(); }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
