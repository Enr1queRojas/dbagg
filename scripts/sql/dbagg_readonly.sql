-- Login de solo lectura para dbagg: SELECT únicamente sobre las fuentes del catálogo
-- (src/dbagg/database/catalog.json). Lo ejecuta un administrador; dbagg nunca lo corre.
-- Sustituye <BASE> y <CONTRASEÑA_LARGA_ALEATORIA>, y después pon DB_USER/DB_PASSWORD del
-- login nuevo en .env. tests/unit/test_readonly_grants.py falla si el catálogo cambia y
-- este archivo no.

USE [master];
CREATE LOGIN dbagg_lectura WITH PASSWORD = N'<CONTRASEÑA_LARGA_ALEATORIA>',
    CHECK_POLICY = ON, DEFAULT_DATABASE = [<BASE>];
GO

USE [<BASE>];
CREATE USER dbagg_lectura FOR LOGIN dbagg_lectura;
-- Sin db_datareader: no ve el resto de tablas (login_access_data, migraciones, etc.).
DENY EXECUTE TO dbagg_lectura;
DENY INSERT, UPDATE, DELETE, ALTER, REFERENCES TO dbagg_lectura;
DENY VIEW DATABASE STATE TO dbagg_lectura;
DENY SHOWPLAN TO dbagg_lectura;

GRANT SELECT ON proadel.ALMACENES_DISPONIBLES_V TO dbagg_lectura;
GRANT SELECT ON proadel.CATALOGO_CLIENTES_DATA_V TO dbagg_lectura;
GRANT SELECT ON proadel.CATALOGO_GASTOS_DATA_V TO dbagg_lectura;
GRANT SELECT ON proadel.CATALOGO_INSUMOS_DATA_V TO dbagg_lectura;
GRANT SELECT ON proadel.CATALOGO_PRODUCTOS_DATA_V TO dbagg_lectura;
GRANT SELECT ON proadel.COBRANZA_DATA TO dbagg_lectura;
GRANT SELECT ON proadel.CATALOGO_PROVEEDORES_DATA_V TO dbagg_lectura;
GRANT SELECT ON proadel.COMPRAS_DATA TO dbagg_lectura;
GRANT SELECT ON proadel.PAGO_PROVEEDOR_DATA TO dbagg_lectura;
GRANT SELECT ON proadel.CONTADO_DATA TO dbagg_lectura;
GRANT SELECT ON proadel.CREDITO_DATA TO dbagg_lectura;
GRANT SELECT ON proadel.DEV_VENTA_DATA TO dbagg_lectura;
GRANT SELECT ON proadel.FRECUENCIA_PEDIDOS_V TO dbagg_lectura;
GRANT SELECT ON proadel.GASTOS_DATA TO dbagg_lectura;
GRANT SELECT ON proadel.PROXIMA_COMPRA_MAIZ_V TO dbagg_lectura;
GRANT SELECT ON proadel.PROYECCION_AGOTAMIENTO_PRODUCTO_V TO dbagg_lectura;
GRANT SELECT ON proadel.RESUMEN_COBRANZA_V TO dbagg_lectura;
GRANT SELECT ON proadel.RESUMEN_CONSUMO_MAIZ TO dbagg_lectura;
GRANT SELECT ON proadel.TOTAL_COBRANZA_CLIENTES_V TO dbagg_lectura;
GRANT SELECT ON proadel.TOTAL_CREDITO_CLIENTES_V TO dbagg_lectura;
GRANT SELECT ON proadel.TOTAL_DEV_VENTA_CLIENTES_V TO dbagg_lectura;
GRANT SELECT ON proadel.TOTAL_GASTOS_BANCOS_V TO dbagg_lectura;
GRANT SELECT ON proadel.TOTAL_GASTOS_GASTOS_V TO dbagg_lectura;
GRANT SELECT ON proadel.TOTAL_PROCESOS_PRODUCTOS_ENTRADAS_V TO dbagg_lectura;
GRANT SELECT ON proadel.TOTAL_PROCESOS_PRODUCTOS_SALIDAS_V TO dbagg_lectura;
GRANT SELECT ON proadel.TOTAL_PROCESOS_PRODUCTOS_V TO dbagg_lectura;
GRANT SELECT ON proadel.ULTIMO_PEDIDO_PRODUCTO_V TO dbagg_lectura;
GRANT SELECT ON proadel.VENTAS_DIARIAS_V TO dbagg_lectura;
GRANT SELECT ON proadel.VW_ALERTASCOBRANZA TO dbagg_lectura;
-- Bancos: catálogo, desglose y tablas de movimientos por cuenta.
GRANT SELECT ON proadel.ABONO_DINERO_DATA TO dbagg_lectura;
GRANT SELECT ON proadel.CATALOGO_BANCOS_DATA TO dbagg_lectura;
GRANT SELECT ON proadel.CATALOGO_BANCOS_DATA_V TO dbagg_lectura;
GRANT SELECT ON proadel.INSUMO_CONTADO_DATA TO dbagg_lectura;
GRANT SELECT ON proadel.PAGO_PRESTAMO_DATA TO dbagg_lectura;
GRANT SELECT ON proadel.PAGO_TRANSPORTE_DATA TO dbagg_lectura;
GRANT SELECT ON proadel.POR_ABONAR_DATA TO dbagg_lectura;
GRANT SELECT ON proadel.POR_APLICAR_DATA TO dbagg_lectura;
GRANT SELECT ON proadel.PRESTAMO_DATA TO dbagg_lectura;
GRANT SELECT ON proadel.PRESTAMO_DINERO_DATA TO dbagg_lectura;
GRANT SELECT ON proadel.RENTAS_DATA TO dbagg_lectura;
GRANT SELECT ON proadel.TRANSFERENCIAS_BANCOS_DATA TO dbagg_lectura;
GO

-- Verificación: debe devolver 0, 0, 0, 0 y solo las vistas de arriba.
EXECUTE AS USER = 'dbagg_lectura';
SELECT IS_SRVROLEMEMBER('sysadmin'), IS_MEMBER('db_owner'),
       IS_MEMBER('db_datawriter'), IS_MEMBER('db_ddladmin');
SELECT s.name + '.' + o.name FROM sys.objects o JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE o.type IN ('U', 'V') ORDER BY 1;
REVERT;
GO
