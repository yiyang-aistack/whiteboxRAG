"""
API unit tests
"""
import sys
from pathlib import Path

# Add project root to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient


class TestKnowledgeAPI:
    """Knowledge base API tests"""

    @pytest.fixture
    def client(self):
        """Create test client"""
        from api.api import app
        return TestClient(app)

    def test_health_check(self, client):
        """Test health check endpoint"""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_list_knowledge_bases(self, client):
        """Test get knowledge base list"""
        response = client.get("/api/knowledge/list")
        assert response.status_code == 200
        data = response.json()
        assert "success" in data
        assert "data" in data

    def test_create_knowledge_base(self, client):
        """Test create knowledge base"""
        response = client.post("/api/knowledge/create", json={
            "name": "测试知识库",
            "description": "测试用知识库"
        })
        assert response.status_code == 200
        data = response.json()
        if data.get("success"):
            assert "kb_id" in data

    def test_create_knowledge_base_empty_name(self, client):
        """Test create with empty name fails"""
        response = client.post("/api/knowledge/create", json={
            "name": "",
            "description": ""
        })
        # Should return validation error
        assert response.status_code in [400, 422]


class TestChatAPI:
    """Chat API tests"""

    @pytest.fixture
    def client(self):
        from api.api import app
        return TestClient(app)

    def test_chat_health(self, client):
        """Test chat health check"""
        response = client.get("/api/chat/health")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data or "message" in data


class TestMonitorAPI:
    """Monitor API tests"""

    @pytest.fixture
    def client(self):
        from api.api import app
        return TestClient(app)

    def test_get_stats(self, client):
        """Test get performance stats"""
        response = client.get("/api/monitor/stats")
        assert response.status_code == 200
        data = response.json()
        assert data.get("success") == True

    def test_get_logs(self, client):
        """Test get logs"""
        response = client.get("/api/monitor/logs?lines=10")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    def test_get_tasks(self, client):
        """Test get task list"""
        response = client.get("/api/monitor/tasks")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data


class TestCORS:
    """CORS cross-origin tests"""

    @pytest.fixture
    def client(self):
        from api.api import app
        return TestClient(app)

    def test_cors_headers(self, client):
        """Test CORS headers"""
        response = client.options("/api/health", headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET"
        })
        # FastAPI CORSMiddleware handles OPTIONS requests


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
