// Captura de assinatura em canvas (mouse, caneta ou dedo).
function iniciarAssinatura(canvasId, limparBtnId) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return null;
  const ctx = canvas.getContext("2d");
  ctx.lineWidth = 2.4;
  ctx.lineCap = "round";
  ctx.strokeStyle = "#0b2545";

  let desenhando = false;
  let ultimoPonto = null;
  let temTraco = false;

  function posicaoRelativa(evento) {
    const rect = canvas.getBoundingClientRect();
    const escalaX = canvas.width / rect.width;
    const escalaY = canvas.height / rect.height;
    const ponto = evento.touches ? evento.touches[0] : evento;
    return {
      x: (ponto.clientX - rect.left) * escalaX,
      y: (ponto.clientY - rect.top) * escalaY,
    };
  }

  function comecar(evento) {
    evento.preventDefault();
    desenhando = true;
    ultimoPonto = posicaoRelativa(evento);
  }

  function desenhar(evento) {
    if (!desenhando) return;
    evento.preventDefault();
    const ponto = posicaoRelativa(evento);
    ctx.beginPath();
    ctx.moveTo(ultimoPonto.x, ultimoPonto.y);
    ctx.lineTo(ponto.x, ponto.y);
    ctx.stroke();
    ultimoPonto = ponto;
    temTraco = true;
  }

  function parar() {
    desenhando = false;
  }

  canvas.addEventListener("mousedown", comecar);
  canvas.addEventListener("mousemove", desenhar);
  window.addEventListener("mouseup", parar);
  canvas.addEventListener("touchstart", comecar, { passive: false });
  canvas.addEventListener("touchmove", desenhar, { passive: false });
  canvas.addEventListener("touchend", parar);

  const botaoLimpar = document.getElementById(limparBtnId);
  if (botaoLimpar) {
    botaoLimpar.addEventListener("click", () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      temTraco = false;
    });
  }

  return {
    estaVazia: () => !temTraco,
    paraDataURL: () => canvas.toDataURL("image/png"),
  };
}
