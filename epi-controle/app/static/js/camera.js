// Captura de foto via câmera (getUserMedia) com fallback para upload de arquivo
// em navegadores/dispositivos sem suporte a webcam direta.
function iniciarCamera(opcoes) {
  const video = document.getElementById(opcoes.videoId);
  const canvas = document.getElementById(opcoes.canvasId);
  const preview = document.getElementById(opcoes.previewId);
  const botaoAbrir = document.getElementById(opcoes.abrirBtnId);
  const botaoTirar = document.getElementById(opcoes.tirarBtnId);
  const botaoRefazer = document.getElementById(opcoes.refazerBtnId);
  const inputArquivo = document.getElementById(opcoes.arquivoInputId);

  let fotoDataUrl = null;
  let streamAtual = null;

  function mostrarSomente(elementoVisivel) {
    [video, canvas, preview].forEach((el) => { if (el) el.hidden = true; });
    if (elementoVisivel) elementoVisivel.hidden = false;
  }

  async function abrirCamera() {
    try {
      streamAtual = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user" }, audio: false,
      });
      video.srcObject = streamAtual;
      mostrarSomente(video);
      botaoTirar.hidden = false;
      botaoAbrir.hidden = true;
    } catch (erro) {
      // Sem câmera disponível/negada: cai para o input de arquivo (abre a câmera do celular).
      inputArquivo.hidden = false;
      botaoAbrir.hidden = true;
    }
  }

  function pararStream() {
    if (streamAtual) {
      streamAtual.getTracks().forEach((t) => t.stop());
      streamAtual = null;
    }
  }

  function tirarFoto() {
    canvas.width = video.videoWidth || 480;
    canvas.height = video.videoHeight || 360;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    fotoDataUrl = canvas.toDataURL("image/png");
    preview.src = fotoDataUrl;
    mostrarSomente(preview);
    pararStream();
    botaoTirar.hidden = true;
    botaoRefazer.hidden = false;
  }

  function refazer() {
    fotoDataUrl = null;
    botaoRefazer.hidden = true;
    botaoAbrir.hidden = false;
    mostrarSomente(null);
  }

  if (inputArquivo) {
    inputArquivo.addEventListener("change", () => {
      const arquivo = inputArquivo.files[0];
      if (!arquivo) return;
      const leitor = new FileReader();
      leitor.onload = (e) => {
        fotoDataUrl = e.target.result;
        preview.src = fotoDataUrl;
        mostrarSomente(preview);
        botaoRefazer.hidden = false;
        inputArquivo.hidden = true;
      };
      leitor.readAsDataURL(arquivo);
    });
  }

  botaoAbrir.addEventListener("click", abrirCamera);
  botaoTirar.addEventListener("click", tirarFoto);
  botaoRefazer.addEventListener("click", refazer);

  return { temFoto: () => !!fotoDataUrl, paraDataURL: () => fotoDataUrl };
}
