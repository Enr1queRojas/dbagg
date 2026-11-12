# Operación seleccionada: saldos_coincidencias
- saldos_coincidencias: saldo actual de cada cliente que coincide con un nombre o código, listado
  por separado y sin sumar. Úsalo cuando pidan los saldos de varios homónimos ("los 3 Alejandro
  Esparza", "todos los García"); customer es ese nombre tal como aparece en la conversación.
Ejemplo (saldos_coincidencias): «Saldo de cada Luis Ficticio» →
  consultar_negocio(operation="saldos_coincidencias", customer="Luis Ficticio").
