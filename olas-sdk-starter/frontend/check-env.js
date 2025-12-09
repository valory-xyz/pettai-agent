#!/usr/bin/env node

/**
 * Environment variable checker for frontend
 * Ensures .env file exists and contains required variables
 */

const fs = require('fs');
const path = require('path');

const ENV_FILE = path.join(__dirname, '.env');
const ENV_EXAMPLE_FILE = path.join(__dirname, '.env.example');
const REQUIRED_VARS = ['REACT_APP_PRIVY_APP_ID'];

function checkEnvFile() {
  let hasErrors = false;
  const missingVars = [];
  const emptyVars = [];

  // Check if .env file exists
  if (!fs.existsSync(ENV_FILE)) {
    console.log('📝 .env file not found. Creating from .env.example...');
    
    if (fs.existsSync(ENV_EXAMPLE_FILE)) {
      fs.copyFileSync(ENV_EXAMPLE_FILE, ENV_FILE);
      console.log('✅ Created .env file from .env.example');
      console.log('⚠️  Please update .env with your actual values before building!\n');
      // Continue to check the newly created file
    } else {
      console.error('❌ .env.example file not found!');
      console.log('Creating a basic .env file...');
      const basicEnv = REQUIRED_VARS.map(varName => `${varName}=`).join('\n') + '\n';
      fs.writeFileSync(ENV_FILE, basicEnv);
      console.log('✅ Created basic .env file');
      console.log('⚠️  Please add your environment variables before building!\n');
      // Continue to check the newly created file
    }
  }

  // Read and parse .env file
  const envContent = fs.readFileSync(ENV_FILE, 'utf8');
  const envVars = {};

  envContent.split('\n').forEach((line) => {
    const trimmed = line.trim();
    if (trimmed && !trimmed.startsWith('#')) {
      const [key, ...valueParts] = trimmed.split('=');
      if (key) {
        envVars[key.trim()] = valueParts.join('=').trim();
      }
    }
  });

  // Check for required variables
  REQUIRED_VARS.forEach((varName) => {
    if (!(varName in envVars)) {
      missingVars.push(varName);
      hasErrors = true;
    } else if (!envVars[varName] || envVars[varName] === 'your_privy_app_id_here') {
      emptyVars.push(varName);
      hasErrors = true;
    }
  });

  // Report issues
  if (missingVars.length > 0) {
    console.error(`❌ Missing required environment variables: ${missingVars.join(', ')}`);
  }

  if (emptyVars.length > 0) {
    console.error(`❌ Empty or placeholder values found: ${emptyVars.join(', ')}`);
    console.error('   Please update these values in .env file');
  }

  if (!hasErrors) {
    console.log('✅ All required environment variables are set');
    return true;
  }

  console.log(`\n📝 Please update your .env file with the required variables.`);
  console.log(`   See .env.example for reference.\n`);
  return false;
}

// Run check
if (require.main === module) {
  const isValid = checkEnvFile();
  process.exit(isValid ? 0 : 1);
}

module.exports = { checkEnvFile };

