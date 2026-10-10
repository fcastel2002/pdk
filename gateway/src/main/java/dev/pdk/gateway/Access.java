package dev.pdk.gateway;

import com.inductiveautomation.ignition.gateway.dataroutes.RequestContext;
import com.inductiveautomation.ignition.gateway.dataroutes.RouteAccess;
import jakarta.servlet.http.HttpServletRequest;

import java.net.InetAddress;
import java.net.UnknownHostException;
import java.util.List;
import java.util.regex.Pattern;

/** Every route: loopback connection, no proxy forwarding headers, and the startup token. */
final class Access {
    private static final List<String> FORWARDING_HEADERS = List.of("Forwarded", "X-Forwarded-For", "X-Real-IP");
    private static final Pattern IP_LITERAL = Pattern.compile("[0-9A-Fa-f:.]+");

    private final Token token;

    Access(Token token) {
        this.token = token;
    }

    RouteAccess check(RequestContext context) {
        HttpServletRequest request = context.getRequest();
        if (!isLoopback(request.getRemoteAddr()) || !isLoopback(request.getLocalAddr())
                || FORWARDING_HEADERS.stream().anyMatch(name -> request.getHeader(name) != null)) {
            return RouteAccess.FORBIDDEN;
        }
        return token.matches(request.getHeader(Token.HEADER)) ? RouteAccess.GRANTED : RouteAccess.UNAUTHORIZED;
    }

    /** True for 127.0.0.0/8 and ::1 in any notation; never resolves a host name. */
    static boolean isLoopback(String address) {
        if (address == null) {
            return false;
        }
        String literal = address.strip();
        if (literal.startsWith("[") && literal.endsWith("]")) {
            literal = literal.substring(1, literal.length() - 1);
        }
        int zone = literal.indexOf('%');
        if (zone >= 0) {
            literal = literal.substring(0, zone);
        }
        if (!IP_LITERAL.matcher(literal).matches()) {
            return false;
        }
        try {
            return InetAddress.getByName(literal).isLoopbackAddress();
        } catch (UnknownHostException error) {
            return false;
        }
    }
}
