const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

// A análise unificada pode percorrer a fila de LLMs (até 7 modelos x 15 s) antes de responder.
const ANALISE_MS = 300000;

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
    await page.getByRole("heading", { name: "Resultado", exact: true }).waitFor({ timeout: ANALISE_MS });
    await tab("Qiskit").click();
    await page.getByText("Circuitos equivalentes", { exact: true }).waitFor();
    await tab("Diff").click();
    await button("Aprovar este arquivo").click();
    await page.getByRole("dialog").getByText("otimizado.qasm", { exact: true }).waitFor();
    await page.getByRole("dialog").getByRole("button", { name: "Adicionar código" }).click();
    const download = page.waitForEvent("download");
    await button("Exportar JSON").click();
    assert.match((await download).suggestedFilename(), /\.json$/);
    // Circuitos inválidos: POST /analise devolve o diagnóstico no cartão "Circuitos analisados".
    const resultado = () => page.locator(".analysis-result-panel").last();
    await circuit("OpenQASM 2", header + "h q[12];");
    await page.locator(".analysis-circuit").first().waitFor({ timeout: ANALISE_MS });
    await resultado().getByText(/linha 4\b/i).first().waitFor();
    const python = "from qiskit import QuantumCircuit\nqc=QuantumCircuit(2)\nqc.h(0)\nqc.cx(0,1)";
    await circuit("Python", python);
    await page.getByRole("heading", { name: "Resultado", exact: true }).waitFor({ timeout: ANALISE_MS });
    await circuit("Python", python + "\nqc.h(8)");
    await page.locator(".analysis-circuit").first().waitFor({ timeout: ANALISE_MS });
    await resultado().getByText(/linha 5\b/i).first().waitFor();
    await circuit("Python", "from qiskit import QuantumCircuit\nfrom qiskit.circuit import Parameter");
    await resultado().getByText(/Parameter não é suportado/).first().waitFor({ timeout: ANALISE_MS });
    await button("Limpar").click();
    // A ferramenta Terminal foi removida do Desktop Chat (o CLI continua separado).
    assert.equal(await page.getByRole("tab", { name: "Terminal" }).count(), 0);
    assert.equal(await page.getByText("Buscar também na web").count(), 0);
    assert.equal(await page.getByRole("checkbox", { name: "Pesquisar também na internet" }).isChecked(), true);
    // Anexar pasta: caminhos relativos, pastas de dependências e tipos não suportados ficam de fora.
    const pasta = fs.mkdtempSync(path.join(os.tmpdir(), "qone-pasta-"));
    const projeto = path.join(pasta, "projeto");
    fs.mkdirSync(path.join(projeto, "src"), { recursive: true });
    fs.mkdirSync(path.join(projeto, "node_modules", "pacote"), { recursive: true });
    fs.writeFileSync(path.join(projeto, "src", "circuito.qasm"), header + "h q[0];\n");
    fs.writeFileSync(path.join(projeto, "README.md"), "# Projeto de teste\n");
    fs.writeFileSync(path.join(projeto, "node_modules", "pacote", "modulo.py"), "print('ignorado')\n");
    fs.writeFileSync(path.join(projeto, "imagem.png"), Buffer.from([0x89, 0x50, 0x4e, 0x47]));
    try {
      await page.locator("input[type=file][webkitdirectory]").setInputFiles(projeto);
      await page.getByText(/2 arquivo\(s\) adicionado\(s\) da pasta projeto\. 2 ignorado\(s\)/).waitFor();
      await page.getByText("projeto/src/circuito.qasm", { exact: true }).first().waitFor();
      assert.equal(await page.getByText("projeto/node_modules/pacote/modulo.py", { exact: true }).count(), 0);
    } finally { fs.rmSync(pasta, { recursive: true, force: true }); }
    await button("Limpar").click();
    await button("Projetos").click();
    await page.locator(".project-create-button").click();
    await page.getByPlaceholder("Ex.: Otimização de circuitos").fill("Teste de integração");
    await page.getByRole("dialog").locator("button[type=submit]").click();
    await page.getByRole("heading", { name: "Teste de integração" }).waitFor();
    await page.locator(".project-open-button").first().click();
    await page.getByText("PROJETO ATIVO", { exact: true }).waitFor();
    await page.getByRole("combobox", { name: "Fontes por consulta" }).selectOption("8");
    const turns = [];
    const mocked = process.env.SKIP_LIVE_LLM === "1";
    const conversaRemota = "9b2f6a8e-3c1d-4e5f-8a7b-6c5d4e3f2a1b";
    if (mocked) {
      // Progresso ao vivo: o cartão de andamento mostra a etapa e o modelo em uso.
      await page.route("**/api/analise/progresso/*", route => route.fulfill({ json: {
        progress_id: route.request().url().split("/").pop(), concluido: false,
        etapa: "Consultando nvidia/modelo-de-teste (1/7)", modelo: "nvidia/modelo-de-teste",
        segundos_na_etapa: 1.5, segundos_total: 3, historico: [{ texto: "Base local consultada", segundos: 0.4 }],
      } }));
      await page.route("**/api/analise", async route => {
        const input = route.request().postDataJSON();
        assert.equal(input.top_k, 8);
        assert.equal(input.pesquisa_web, true); // "Pesquisar também na internet" vem marcado
        assert.ok(input.arquivos.some(arquivo => arquivo.nome === "nota.txt" && /Contexto da amostra/.test(arquivo.conteudo)));
        assert.match(input.progress_id, /^[0-9a-f-]{36}$/i);
        assert.match(input.mensagem, /\S/);
        turns.push(input.conversation_id);
        await new Promise(resolve => setTimeout(resolve, 2500)); // tempo para o andamento aparecer
        return route.fulfill({ json: {
          request_id: "5d8f2c1a-7b3e-4a9d-8c6f-0e1d2c3b4a59", progress_id: input.progress_id, conversation_id: conversaRemota,
          resposta: "Resposta de teste.\n```python\nqc.h(0)\n```", status: "ok", answer_mode: "llm",
          llm: { provedor: "nvidia", modelo: "nvidia/modelo-de-teste", tentativas: [{ modelo: "nvidia/modelo-de-teste", resultado: "ok", segundos: 1.2 }] },
          sources: [{ document_id: "manual", chunk_id: "1", source: "https://example.org", title: "Manual", content: "Trecho", origin: "local", retrieved_at: null }],
          circuitos: [], arquivos: [{ nome: "nota.txt", tipo: "documento", tamanho: 20, motivo: null }],
          interpretation: null, evidence: null, pesquisa_web: null, warnings: [],
          etapas: [{ texto: "Base local consultada", segundos: 0.4 }], tempo_total_segundos: 1.6,
        } });
      });
    }
    await page.locator('input[type=file]').first().setInputFiles({ name: "nota.txt", mimeType: "text/plain", buffer: Buffer.from("Contexto da amostra.") });
    await page.locator("#analysis-question").fill("Explique a transpilação no Qiskit.");
    await button("Analisar").click();
    if (mocked) await page.getByRole("status").filter({ hasText: "Modelo em uso:" }).getByText("nvidia/modelo-de-teste").waitFor();
    await page.getByRole("heading", { name: "Resultado", exact: true }).waitFor({ timeout: ANALISE_MS });
    if (mocked) {
      await page.locator(".analysis-answer-code").first().waitFor();
      await page.getByText("Tentativas dos modelos (1)", { exact: true }).waitFor();
    }
    // Fontes compactas: recolhidas por padrão e expansíveis.
    const fontes = page.getByRole("button", { name: /Fontes consultadas \(\d+\)/ }).first();
    await fontes.waitFor();
    assert.equal(await fontes.getAttribute("aria-expanded"), "false");
    await fontes.click();
    assert.equal(await fontes.getAttribute("aria-expanded"), "true");
    await tab("Fontes").click();
    await page.locator(".workspace-source-card").first().click();
    await page.getByRole("dialog").waitFor();
    await page.keyboard.press("Escape");
    await page.locator("#analysis-question").fill("Como a profundidade é medida?");
    await button("Analisar").click();
    await page.locator(".analysis-result-panel").nth(1).waitFor({ timeout: ANALISE_MS });
    if (mocked) {
      // A segunda pergunta continua a mesma conversa no servidor.
      assert.deepEqual(turns, [null, conversaRemota]);
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
