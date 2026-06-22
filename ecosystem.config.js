const fs = require("fs");
const path = require("path");

function serviceDirectory(environmentName, awsDefault) {
  if (process.env[environmentName]) {
    return process.env[environmentName];
  }
  return fs.existsSync(awsDefault) ? awsDefault : __dirname;
}

const frontendDirectory = serviceDirectory(
  "FRONTEND_APP_DIR",
  "/var/www/frontend_api",
);
const sapDirectory = serviceDirectory(
  "SAP_API_APP_DIR",
  "/var/www/backend_sap_api",
);
const agentDirectory = serviceDirectory(
  "AGENT_API_APP_DIR",
  "/var/www/backend_agent_api",
);

for (const directory of [frontendDirectory, sapDirectory, agentDirectory]) {
  fs.mkdirSync(path.join(directory, "logs"), { recursive: true });
}

function appDefinition(name, directory, startupScript, environment) {
  return {
    name,
    cwd: directory,
    script: path.join(directory, "scripts", startupScript),
    interpreter: "/bin/bash",
    env: environment,
    autorestart: true,
    restart_delay: 5000,
    max_restarts: 10,
    time: true,
    out_file: path.join(directory, "logs", `${name}.out.log`),
    error_file: path.join(directory, "logs", `${name}.error.log`),
  };
}

module.exports = {
  apps: [
    appDefinition(
      "frontend_api",
      frontendDirectory,
      "start_frontend.sh",
      {
        FRONTEND_PORT: process.env.FRONTEND_PORT || "3000",
      },
    ),
    appDefinition(
      "backend_sap_api",
      sapDirectory,
      "start_sap_api.sh",
      {
        SAP_API_PORT: process.env.SAP_API_PORT || "3003",
      },
    ),
    appDefinition(
      "backend_agent_api",
      agentDirectory,
      "start_agent_api.sh",
      {
        AGENT_API_PORT: process.env.AGENT_API_PORT || "3006",
      },
    ),
  ],
};
