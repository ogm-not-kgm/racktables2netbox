-- Create DEFINER user required by RackTables views and triggers
CREATE USER IF NOT EXISTS 'racktables_user'@'127.0.0.1' IDENTIFIED BY 'PASSWORD';
CREATE USER IF NOT EXISTS 'racktables_user'@'%' IDENTIFIED BY 'PASSWORD';
GRANT ALL PRIVILEGES ON *.* TO 'racktables_user'@'127.0.0.1';
GRANT ALL PRIVILEGES ON *.* TO 'racktables_user'@'%';

-- Ensure migration user has full privileges
GRANT ALL PRIVILEGES ON *.* TO 'netbox'@'%';
FLUSH PRIVILEGES;
