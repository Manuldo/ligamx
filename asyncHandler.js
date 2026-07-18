// Envuelve rutas async para que un error rechace hacia el manejador
// de errores de Express en vez de tumbar el proceso.
export const ah = (fn) => (req, res, next) =>
  Promise.resolve(fn(req, res, next)).catch(next);
