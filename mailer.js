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
