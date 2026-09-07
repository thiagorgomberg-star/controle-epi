const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = "http://127.0.0.1:5000";
const OUT = "/tmp/demo-screens";
fs.mkdirSync(OUT, { recursive: true });
const shot = (name) => path.join(OUT, name);

(async () => {
  const browser = await chromium.launch({
    executablePath: "/opt/pw-browsers/chromium",
    args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
  });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    permissions: ["camera"],
  });
  const page = await context.newPage();

  // 1) Cadastro do administrador (primeiro acesso)
  await page.goto(`${BASE}/auth/cadastro`);
  await page.fill("#nome", "Thiago Gomberg");
  await page.fill("#email", "thiago@multivaleengenharia.com.br");
  await page.fill("#matricula", "0001");
  await page.fill("#cargo", "Técnico de Segurança do Trabalho");
  await page.fill("#setor", "SESMT");
  await page.fill("#senha", "senha12345");
  await page.fill("#senha_confirma", "senha12345");
  await page.click('button[type=submit]');
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: shot("01-painel-sesmt.png"), fullPage: true });
  console.log("OK 1 - painel sesmt");

  // 2) Cadastro de EPI com autopreenchimento por CA
  await page.goto(`${BASE}/epis/novo`);
  await page.click("#ca_numero");
  await page.type("#ca_numero", "26449", { delay: 80 });
  await page.waitForTimeout(900); // aguarda o debounce + busca local
  await page.screenshot({ path: shot("02-cadastro-epi-autopreenchido.png"), fullPage: true });
  await page.fill("#estoque_inicial", "15");
  await page.fill("#estoque_minimo", "3");
  await page.click('button:has-text("Salvar EPI")');
  await page.waitForLoadState("networkidle");
  console.log("OK 2 - epi 1 criado");

  await page.goto(`${BASE}/epis/novo`);
  await page.click("#ca_numero");
  await page.type("#ca_numero", "45452", { delay: 80 });
  await page.waitForTimeout(900);
  await page.fill("#estoque_inicial", "8");
  await page.fill("#estoque_minimo", "2");
  await page.click('button:has-text("Salvar EPI")');
  await page.waitForLoadState("networkidle");

  await page.goto(`${BASE}/epis/`);
  await page.screenshot({ path: shot("03-lista-epis.png"), fullPage: true });
  console.log("OK 3 - lista de epis");

  // 3) Cadastro de colaborador
  await page.goto(`${BASE}/colaboradores/`);
  await page.fill("#nome", "Ana Paula Ferreira");
  await page.fill("#email", "ana.ferreira@multivaleengenharia.com.br");
  await page.fill("#matricula", "10234");
  await page.fill("#cargo", "Eletricista");
  await page.fill("#setor", "Manutenção");
  await page.selectOption("#papel", "colaborador");
  await page.click('button:has-text("Cadastrar colaborador")');
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: shot("04-lista-colaboradores.png"), fullPage: true });
  const flashTexto = await page.locator(".flash.sucesso").first().textContent();
  const senhaMatch = flashTexto && flashTexto.match(/Senha provisória:\s*(\S+)/);
  const senhaColaborador = senhaMatch ? senhaMatch[1] : null;
  console.log("OK 4 - colaborador criado, senha provisória:", senhaColaborador);

  // 4) Direcionar entrega
  await page.goto(`${BASE}/entregas/`);
  await page.selectOption("#colaborador_id", { label: "Ana Paula Ferreira — 10234" });
  await page.selectOption("#epi_id", { index: 1 });
  await page.fill("#quantidade", "1");
  await page.fill("#tamanho", "39");
  await page.click('button:has-text("Registrar entrega")');
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: shot("05-lista-entregas.png"), fullPage: true });
  console.log("OK 5 - entrega direcionada");

  // 5) Logout do admin, login como colaborador
  await page.goto(`${BASE}/auth/sair`);
  await page.goto(`${BASE}/auth/login`);
  await page.fill("#email", "ana.ferreira@multivaleengenharia.com.br");
  await page.fill("#senha", senhaColaborador || "");
  await page.click('button[type=submit]');
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: shot("06-painel-colaborador.png"), fullPage: true });
  console.log("OK 6 - painel do colaborador (pendencia)");

  // 6) Tela de aceite: assinatura + foto
  const linkAceitar = await page.locator('a[href*="/aceitar"]').first().getAttribute("href").catch(() => null);
  const urlAceitar = linkAceitar ? `${BASE}${linkAceitar}` : null;
  if (urlAceitar) {
    await page.goto(urlAceitar);

    // Assina no canvas (alguns traços de mouse)
    const canvasBox = await page.locator("#canvas-assinatura").boundingBox();
    await page.mouse.move(canvasBox.x + 40, canvasBox.y + 90);
    await page.mouse.down();
    for (const [dx, dy] of [[60, -30], [60, 40], [60, -50], [60, 30]]) {
      await page.mouse.move(canvasBox.x + 40 + dx, canvasBox.y + 90 + dy, { steps: 6 });
    }
    await page.mouse.up();

    // Abre a câmera (dispositivo falso do Chromium) e tira a foto
    await page.click("#btn-abrir-camera");
    await page.waitForTimeout(700);
    await page.click("#btn-tirar-foto");
    await page.waitForTimeout(300);

    await page.screenshot({ path: shot("07-tela-aceite.png"), fullPage: true });
    console.log("OK 7 - tela de aceite (assinatura + foto)");

    await page.click('button:has-text("Confirmar aceite")');
    await page.waitForLoadState("networkidle");
    console.log("OK 8 - aceite confirmado, url final:", page.url());
  } else {
    console.log("AVISO: não encontrei o link de aceite");
  }

  // 7) Confirma status "Aceito" no painel do SESMT
  await page.goto(`${BASE}/auth/sair`);
  await page.goto(`${BASE}/auth/login`);
  await page.fill("#email", "thiago@multivaleengenharia.com.br");
  await page.fill("#senha", "senha12345");
  await page.click('button[type=submit]');
  await page.waitForLoadState("networkidle");
  await page.goto(`${BASE}/entregas/`);
  await page.screenshot({ path: shot("08-entregas-aceito.png"), fullPage: true });
  console.log("OK 9 - entrega marcada como aceita");

  await browser.close();
  console.log("FIM");
})().catch((err) => {
  console.error("Falhou:", err);
  process.exit(1);
});
