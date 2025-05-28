"""
Health check endpoint for the LiveKit agent service.
This helps Render.com monitor the service health and runs the agent.
"""

from flask import Flask, jsonify
import threading
import logging
import os
import sys
import time
import asyncio
from db_driver import DatabaseDriver

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('health.log')
    ]
)

app = Flask(__name__)
logger = logging.getLogger(__name__)

# Global health status
health_status = {
    "status": "starting",
    "database": "unknown",
    "environment": "unknown", 
    "agent": "starting",
    "last_check": None,
    "uptime": 0,
    "error_count": 0,
    "last_error": None
}

start_time = time.time()
agent_thread = None
agent_running = False
agent_error_count = 0
last_agent_restart = 0

def check_database_health():
    """Check database connectivity"""
    try:
        db = DatabaseDriver()
        if db.test_connection():
            return "healthy"
        else:
            return "unhealthy"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return "unhealthy"

def check_environment():
    """Check if all required environment variables are set"""
    required_vars = [
        "LIVEKIT_URL", 
        "LIVEKIT_API_KEY", 
        "LIVEKIT_API_SECRET", 
        "OPENAI_API_KEY", 
        "DATABASE_URL"
    ]
    
    missing_vars = []
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        return f"missing: {', '.join(missing_vars)}"
    
    return "healthy"

def run_agent():
    """Run the LiveKit agent in a separate thread with error recovery"""
    global agent_running, agent_error_count, last_agent_restart
    
    max_retries = 5
    retry_delay = 30  # seconds
    
    while agent_error_count < max_retries:
        try:
            logger.info(f"Starting LiveKit agent thread (attempt {agent_error_count + 1}/{max_retries})...")
            agent_running = True
            
            # Import and run the agent
            from agent import main
            main()
            
            # If we get here, the agent stopped normally
            logger.info("Agent stopped normally")
            break
            
        except KeyboardInterrupt:
            logger.info("Agent stopped by user interrupt")
            break
        except Exception as e:
            agent_error_count += 1
            agent_running = False
            last_agent_restart = time.time()
            
            health_status["error_count"] = agent_error_count
            health_status["last_error"] = str(e)
            
            logger.error(f"Agent thread error (attempt {agent_error_count}/{max_retries}): {e}")
            
            if agent_error_count < max_retries:
                logger.info(f"Restarting agent in {retry_delay} seconds...")
                time.sleep(retry_delay)
                # Exponential backoff
                retry_delay = min(retry_delay * 1.5, 300)  # Max 5 minutes
            else:
                logger.error("Maximum retry attempts reached. Agent will not restart automatically.")
                break
    
    agent_running = False

def restart_agent_if_needed():
    """Restart agent if it's been down for too long"""
    global agent_thread, agent_running, agent_error_count, last_agent_restart
    
    # Only restart if agent has been down for more than 5 minutes and we haven't hit max retries
    if (not agent_running and 
        agent_error_count < 5 and 
        time.time() - last_agent_restart > 300):
        
        logger.info("Attempting to restart agent after extended downtime...")
        
        # Clean up old thread if it exists
        if agent_thread and agent_thread.is_alive():
            try:
                agent_thread.join(timeout=10)
            except:
                pass
        
        # Start new agent thread
        agent_thread = threading.Thread(target=run_agent, daemon=True)
        agent_thread.start()
        last_agent_restart = time.time()

@app.route('/health')
def health_check():
    """Health check endpoint"""
    try:
        # Check database
        db_status = check_database_health()
        
        # Check environment
        env_status = check_environment()
        
        # Calculate uptime
        uptime = int(time.time() - start_time)
        
        # Check if agent needs restart
        restart_agent_if_needed()
        
        # Determine overall health
        overall_healthy = (
            db_status == "healthy" and 
            env_status == "healthy" and
            agent_error_count < 5
        )
        
        # Update health status
        health_status.update({
            "status": "healthy" if overall_healthy else "unhealthy",
            "database": db_status,
            "environment": env_status,
            "agent": "running" if agent_running else f"stopped (errors: {agent_error_count})",
            "last_check": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "uptime": uptime
        })
        
        # Return appropriate HTTP status
        # Still return 200 even if agent has some errors, as long as the service is trying to recover
        status_code = 200 if (db_status == "healthy" and env_status == "healthy") else 503
        
        return jsonify(health_status), status_code
        
    except Exception as e:
        logger.error(f"Health check error: {e}")
        return jsonify({
            "status": "error",
            "error": str(e),
            "uptime": int(time.time() - start_time)
        }), 503

@app.route('/status')
def status():
    """Detailed status endpoint"""
    return jsonify(health_status)

@app.route('/restart')
def restart_agent():
    """Manual restart endpoint for debugging"""
    global agent_thread, agent_running, agent_error_count
    
    try:
        logger.info("Manual agent restart requested")
        
        # Reset error count for manual restart
        agent_error_count = 0
        health_status["error_count"] = 0
        health_status["last_error"] = None
        
        # Stop current agent if running
        if agent_running:
            agent_running = False
            time.sleep(2)  # Give it time to stop
        
        # Clean up old thread
        if agent_thread and agent_thread.is_alive():
            try:
                agent_thread.join(timeout=10)
            except:
                pass
        
        # Start new agent thread
        agent_thread = threading.Thread(target=run_agent, daemon=True)
        agent_thread.start()
        
        return jsonify({
            "status": "success",
            "message": "Agent restart initiated"
        })
        
    except Exception as e:
        logger.error(f"Manual restart error: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

@app.route('/')
def root():
    """Root endpoint"""
    return jsonify({
        "service": "LiveKit Moving Agent",
        "status": health_status["status"],
        "uptime": int(time.time() - start_time),
        "endpoints": ["/health", "/status", "/restart"]
    })

def main():
    """Main function to start both health server and agent"""
    global agent_thread
    
    logger.info("Starting LiveKit Agent with Health Check Server...")
    
    try:
        # Validate environment first
        env_status = check_environment()
        if env_status != "healthy":
            logger.error(f"Environment check failed: {env_status}")
            health_status.update({
                "status": "unhealthy",
                "environment": env_status,
                "agent": "failed_to_start"
            })
        else:
            # Start the agent in a separate thread
            agent_thread = threading.Thread(target=run_agent, daemon=True)
            agent_thread.start()
            logger.info("Agent thread started")
        
        # Get port from environment (Render assigns this)
        port = int(os.getenv('PORT', 8080))
        logger.info(f"Starting health server on port {port}")
        
        # Start the Flask health server (this keeps the service alive)
        app.run(
            host='0.0.0.0', 
            port=port, 
            debug=False,
            threaded=True,
            use_reloader=False  # Disable reloader to prevent issues with threading
        )
        
    except Exception as e:
        logger.error(f"Failed to start service: {e}")
        health_status.update({
            "status": "error",
            "agent": "failed",
            "error": str(e)
        })
        sys.exit(1)

if __name__ == "__main__":
    main()