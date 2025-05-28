"""
Health check endpoint for the LiveKit agent service.
This helps Render.com monitor the service health.
"""

from flask import Flask, jsonify
import threading
import logging
import os
from db_driver import DatabaseDriver

app = Flask(__name__)
logger = logging.getLogger(__name__)

# Global health status
health_status = {
    "status": "starting",
    "database": "unknown",
    "agent": "unknown",
    "last_check": None
}

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
    
    for var in required_vars:
        if not os.getenv(var):
            return f"missing_{var}"
    
    return "healthy"

@app.route('/health')
def health_check():
    """Health check endpoint"""
    try:
        # Check database
        db_status = check_database_health()
        
        # Check environment
        env_status = check_environment()
        
        # Update health status
        health_status.update({
            "status": "healthy" if db_status == "healthy" and env_status == "healthy" else "unhealthy",
            "database": db_status,
            "environment": env_status,
            "agent": "running"
        })
        
        # Return appropriate HTTP status
        status_code = 200 if health_status["status"] == "healthy" else 503
        
        return jsonify(health_status), status_code
        
    except Exception as e:
        logger.error(f"Health check error: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 503

@app.route('/status')
def status():
    """Detailed status endpoint"""
    return jsonify(health_status)

def run_health_server():
    """Run the health check server in a separate thread"""
    port = int(os.getenv('HEALTH_PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == "__main__":
    # Start health server
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    # Import and run the main agent
    from agent import main
    main()