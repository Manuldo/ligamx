// Envio de correo via Resend.
//
// Si no hay RESEND_API_KEY configurada, no truena: devuelve el enlace
// para que se pueda entregar a mano desde el panel. Asi nadie se queda
// sin recuperar su cuenta aunque el correo falle.

function fromAddress() { return process.env.MAIL_FROM || "Pickazo <onboarding@resend.dev>"; }

export function correoConfigurado() {
  return !!process.env.RESEND_API_KEY;
}

export async function enviarCorreo({ para, asunto, html, texto }) {
  if (!correoConfigurado()) {
    return { ok: false, motivo: "sin_configurar" };
  }
  try {
    const r = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${process.env.RESEND_API_KEY}`
      },
      body: JSON.stringify({ from: fromAddress(), to: [para], subject: asunto, html, text: texto })
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      console.error("Resend rechazo el envio:", data?.message || r.status);
      return { ok: false, motivo: "rechazado", detalle: data?.message };
    }
    return { ok: true, id: data.id };
  } catch (err) {
    console.error("Error enviando correo:", err.message);
    return { ok: false, motivo: "error_red" };
  }
}

// Plantilla del correo de recuperacion.
export function plantillaReset({ enlace, minutos }) {
  const html = `<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#14120f;font-family:Arial,Helvetica,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#14120f;padding:32px 16px">
    <tr><td align="center">
      <table width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#1b1e26;border-radius:12px;overflow:hidden">
        <tr><td style="padding:26px 28px 0">
          <div style="font-size:18px;font-weight:bold;color:#eceef4;letter-spacing:-.3px">
            PICKAZO <span style="color:#ec3013">MX</span></div>
        </td></tr>
        <tr><td style="padding:20px 28px 0">
          <h1 style="margin:0 0 10px;font-size:21px;color:#eceef4">Recupera tu contraseña</h1>
          <p style="margin:0 0 20px;font-size:14px;line-height:1.6;color:#8d93a3">
            Pediste restablecer tu contraseña. Toca el botón para elegir una nueva.
            El enlace sirve una sola vez y vence en ${minutos} minutos.</p>
        </td></tr>
        <tr><td style="padding:0 28px 24px">
          <a href="${enlace}" style="display:block;text-align:center;background:#ec3013;color:#ffffff;
            text-decoration:none;font-weight:bold;font-size:15px;padding:14px 20px;border-radius:8px">
            Elegir nueva contraseña</a>
        </td></tr>
        <tr><td style="padding:0 28px 26px">
          <p style="margin:0 0 8px;font-size:12px;color:#5f6474">
            Si el botón no funciona, copia esta dirección en tu navegador:</p>
          <p style="margin:0;font-size:11px;color:#8d93a3;word-break:break-all">${enlace}</p>
        </td></tr>
        <tr><td style="padding:16px 28px;border-top:1px solid #252935">
          <p style="margin:0;font-size:12px;line-height:1.6;color:#5f6474">
            Si no pediste esto, ignora el correo: tu contraseña sigue igual.</p>
        </td></tr>
      </table>
      <p style="margin:18px 0 0;font-size:11px;color:#5f6474">
        Pickazo MX · Juega con responsabilidad · +18</p>
    </td></tr>
  </table>
</body></html>`;

  const texto = `Recupera tu contraseña de Pickazo MX

Pediste restablecer tu contraseña. Abre este enlace para elegir una nueva:
${enlace}

El enlace sirve una sola vez y vence en ${minutos} minutos.
Si no pediste esto, ignora el correo: tu contraseña sigue igual.`;

  return { html, texto };
}

// ---------------------------------------------------------------------------
// Correos del ciclo de compra PRO. Todos usan el mismo diseño que el de
// recuperación. Los datos que vienen de Stripe se escapan antes de insertarse.
// ---------------------------------------------------------------------------

const escHtml = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
));

export function formatoMonto(centavos, moneda = "mxn") {
  return new Intl.NumberFormat("es-MX", { style: "currency", currency: String(moneda).toUpperCase() })
    .format((Number(centavos) || 0) / 100);
}

export function formatoFecha(fecha) {
  if (!fecha) return "—";
  return new Date(fecha).toLocaleDateString("es-MX", {
    day: "numeric", month: "long", year: "numeric", timeZone: "America/Mexico_City",
  });
}

function plantillaBase({ titulo, parrafos, filas = [], boton, nota }) {
  const ps = parrafos.map((p) =>
    `<p style="margin:0 0 14px;font-size:14px;line-height:1.6;color:#8d93a3">${p}</p>`).join("");
  const tabla = filas.length ? `
        <tr><td style="padding:0 28px 18px">
          <table width="100%" cellpadding="0" cellspacing="0" style="background:#14120f;border-radius:8px">
            ${filas.map(([k, v]) => `<tr>
              <td style="padding:10px 14px;font-size:13px;color:#5f6474">${escHtml(k)}</td>
              <td style="padding:10px 14px;font-size:13px;color:#eceef4;text-align:right;font-weight:bold">${escHtml(v)}</td>
            </tr>`).join("")}
          </table>
        </td></tr>` : "";
  const btn = boton ? `
        <tr><td style="padding:0 28px 24px">
          <a href="${escHtml(boton.enlace)}" style="display:block;text-align:center;background:#ec3013;color:#ffffff;
            text-decoration:none;font-weight:bold;font-size:15px;padding:14px 20px;border-radius:8px">
            ${escHtml(boton.texto)}</a>
        </td></tr>` : "";
  const pie = nota ? `
        <tr><td style="padding:16px 28px;border-top:1px solid #252935">
          <p style="margin:0;font-size:12px;line-height:1.6;color:#5f6474">${nota}</p>
        </td></tr>` : "";

  return `<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#14120f;font-family:Arial,Helvetica,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#14120f;padding:32px 16px">
    <tr><td align="center">
      <table width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#1b1e26;border-radius:12px;overflow:hidden">
        <tr><td style="padding:26px 28px 0">
          <div style="font-size:18px;font-weight:bold;color:#eceef4;letter-spacing:-.3px">
            PICKAZO <span style="color:#ec3013">MX</span></div>
        </td></tr>
        <tr><td style="padding:20px 28px 4px">
          <h1 style="margin:0 0 14px;font-size:21px;color:#eceef4">${escHtml(titulo)}</h1>
          ${ps}
        </td></tr>${tabla}${btn}${pie}
      </table>
      <p style="margin:18px 0 0;font-size:11px;color:#5f6474">
        Pickazo MX · Juega con responsabilidad · +18</p>
    </td></tr>
  </table>
</body></html>`;
}

function texto(titulo, lineas, filas = [], boton) {
  return [titulo, "", ...lineas, "", ...filas.map(([k, v]) => `${k}: ${v}`),
    boton ? `\n${boton.texto}: ${boton.enlace}` : "", "", "Pickazo MX · Juega con responsabilidad · +18"]
    .join("\n").replace(/<[^>]+>/g, "");
}

// 1) Primera activación de PRO (checkout completado).
export function correoBienvenidaPro({ appUrl, montoHoy, montoRecurrente, moneda, proximoCobro }) {
  const titulo = "¡Ya eres PRO! 🎉";
  const parrafos = [
    "Gracias por suscribirte a Pickazo PRO. Ya tienes desbloqueados los picks completos, los mercados y momios, los parlays y el historial pick por pick.",
    "Tu suscripción se renueva automáticamente cada mes. Puedes cancelarla cuando quieras desde <b>Mi cuenta</b> y conservas el PRO hasta el final del periodo pagado.",
  ];
  const filas = [
    ["Pagaste hoy", formatoMonto(montoHoy, moneda)],
    ["Siguiente cobro", `${formatoMonto(montoRecurrente, moneda)} el ${formatoFecha(proximoCobro)}`],
  ];
  const boton = { texto: "Ver los picks de hoy", enlace: appUrl };
  const nota = "El recibo oficial de tu pago te llega en un correo aparte de Stripe, nuestro procesador de pagos.";
  return {
    asunto: "Bienvenido a Pickazo PRO",
    html: plantillaBase({ titulo, parrafos, filas, boton, nota }),
    texto: texto(titulo, parrafos, filas, boton),
  };
}

// 2) Falló el cobro de la renovación.
export function correoPagoFallido({ appUrl, monto, moneda, siguienteIntento }) {
  const titulo = "No pudimos cobrar tu PRO";
  const parrafos = [
    `Intentamos cobrar ${escHtml(formatoMonto(monto, moneda))} de tu suscripción y el banco lo rechazó. Mientras se resuelve, tu cuenta PRO queda en pausa.`,
    siguienteIntento
      ? `Volveremos a intentarlo el <b>${escHtml(formatoFecha(siguienteIntento))}</b>. Para no perder tu PRO, actualiza tu tarjeta desde Mi cuenta.`
      : "Ya no habrá más intentos automáticos. Actualiza tu tarjeta desde Mi cuenta para recuperar tu PRO.",
  ];
  const boton = { texto: "Actualizar mi tarjeta", enlace: `${appUrl}/?ver=cuenta` };
  const nota = "Causas comunes: fondos insuficientes, tarjeta vencida o el banco bloqueó el cargo en línea.";
  return {
    asunto: "Tu pago de Pickazo PRO no pasó",
    html: plantillaBase({ titulo, parrafos, boton, nota }),
    texto: texto(titulo, parrafos, [], boton),
  };
}

// 3) El usuario canceló; conserva PRO hasta el fin del periodo.
export function correoCancelacionProgramada({ appUrl, terminaEl }) {
  const titulo = "Cancelaste tu PRO";
  const parrafos = [
    `Listo, tu suscripción ya no se renovará y no se te hará ningún otro cargo. Sigues teniendo PRO hasta el <b>${escHtml(formatoFecha(terminaEl))}</b>.`,
    "Si cambias de opinión antes de esa fecha, puedes reactivarla desde Mi cuenta sin volver a pagar hoy.",
  ];
  const boton = { texto: "Reactivar mi PRO", enlace: `${appUrl}/?ver=cuenta` };
  return {
    asunto: "Confirmación: cancelaste Pickazo PRO",
    html: plantillaBase({ titulo, parrafos, boton }),
    texto: texto(titulo, parrafos, [], boton),
  };
}

// 4) La suscripción terminó (fin del periodo cancelado o pagos fallidos).
export function correoProTerminado({ appUrl, porPagoFallido }) {
  const titulo = "Tu PRO terminó";
  const parrafos = porPagoFallido
    ? ["Como no pudimos cobrar la renovación, tu suscripción PRO se canceló. Tu cuenta sigue activa con el plan gratis."]
    : ["Tu periodo PRO llegó a su fin. Tu cuenta sigue activa con el plan gratis."];
  parrafos.push("Cuando quieras volver, te suscribes en un minuto y recuperas todo al instante.");
  const boton = { texto: "Volver a PRO", enlace: `${appUrl}/?ver=cuenta` };
  return {
    asunto: "Tu Pickazo PRO terminó",
    html: plantillaBase({ titulo, parrafos, boton }),
    texto: texto(titulo, parrafos, [], boton),
  };
}
