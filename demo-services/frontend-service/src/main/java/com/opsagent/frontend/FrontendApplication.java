package com.opsagent.frontend;

import com.opsagent.common.FaultController;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.web.filter.OncePerRequestFilter;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;

@SpringBootApplication
@Import(FaultController.class)
public class FrontendApplication {

    public static void main(String[] args) {
        SpringApplication.run(FrontendApplication.class, args);
    }

    @Bean
    public OncePerRequestFilter faultFilter(FaultController faultController) {
        return new OncePerRequestFilter() {
            @Override
            protected void doFilterInternal(
                    HttpServletRequest request,
                    HttpServletResponse response,
                    FilterChain chain) throws ServletException, IOException {

                if (faultController.isErrorMode()
                        && !request.getRequestURI().startsWith("/fault")
                        && !request.getRequestURI().startsWith("/actuator")) {
                    response.sendError(500, "Fault injected error");
                    return;
                }

                long latency = faultController.getLatencyMs();
                if (latency > 0
                        && !request.getRequestURI().startsWith("/fault")
                        && !request.getRequestURI().startsWith("/actuator")) {
                    try {
                        Thread.sleep(latency);
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                    }
                }

                chain.doFilter(request, response);
            }
        };
    }
}
