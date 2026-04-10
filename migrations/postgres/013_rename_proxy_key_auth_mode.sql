-- ABOUTME: Rename auth_mode value 'proxy_key' to 'client_key' to match the renamed CLIENT_API_KEY config
-- ABOUTME: The concept is unchanged (a shared pre-configured key the gateway accepts from clients)

UPDATE auth_config SET auth_mode = 'client_key' WHERE auth_mode = 'proxy_key';
