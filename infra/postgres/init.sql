-- Runs once, on the first start of an empty postgres volume.
-- The main application database (POSTGRES_DB) is created by the image itself.
CREATE DATABASE marketlens_test;
CREATE DATABASE mlflow;
