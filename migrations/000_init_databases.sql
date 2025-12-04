-- ABOUTME: Initialize two separate databases for LiteLLM and Luthien Control
-- ABOUTME: Creates dedicated users and databases with proper isolation

-- Create the litellm user with its own password
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_user WHERE usename = 'litellm') THEN
        CREATE USER litellm WITH PASSWORD 'litellm_dev_password';
    END IF;
END
$$;

-- Create the litellm database owned by litellm user
-- Note: CREATE DATABASE cannot use IF NOT EXISTS, so we use a workaround
SELECT 'CREATE DATABASE litellm_db OWNER litellm'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'litellm_db')\gexec

-- Grant all privileges on litellm_db to litellm user
GRANT ALL PRIVILEGES ON DATABASE litellm_db TO litellm;

-- The luthien user and luthien_control database are created by Docker environment variables
-- Just ensure luthien has full access to its own database
GRANT ALL PRIVILEGES ON DATABASE luthien_control TO luthien;

-- Prevent cross-database access
-- Revoke any default public access
REVOKE CONNECT ON DATABASE litellm_db FROM PUBLIC;
REVOKE CONNECT ON DATABASE luthien_control FROM PUBLIC;

-- Grant specific access only
GRANT CONNECT ON DATABASE litellm_db TO litellm;
GRANT CONNECT ON DATABASE luthien_control TO luthien;

-- Ensure users can only access their own databases
-- Note: The luthien user is superuser as POSTGRES_USER, but litellm is restricted
