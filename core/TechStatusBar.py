"""
TechStatusBar — lightweight helper that owns the QLabel injected into the
"Technology Configuration" toolbar.  Keeps the label text in sync with the
current tech_config state without creating import cycles.
"""

_label = None   # QLabel reference set by InitGui after toolbar creation


def set_label(lbl):
    global _label
    _label = lbl
    refresh()


def refresh():
    """Rewrite the status label to reflect the current tech_config state."""
    if _label is None:
        return
    try:
        from core.TechConfig import tech_config

        name   = tech_config.get_active_name() or ""
        has_l  = tech_config.has_lyp()
        has_m  = tech_config.has_map()
        has_x  = tech_config.has_xml()

        def _tag(ok, letter):
            color = "#2E7D32" if ok else "#9E9E9E"
            mark  = "✔" if ok else "✘"
            return (
                f'<font color="{color}"><b>{letter}</b>'
                f'<font color="{color}" style="font-size:9px">{mark}</font></font>'
            )

        if name:
            profile_html = (
                f'<font color="#1565C0"><b>{name}</b></font>'
            )
        else:
            profile_html = '<font color="#B71C1C"><i>(no profile)</i></font>'

        if has_l or has_m or has_x:
            file_html = (
                f'&nbsp;&nbsp;'
                f'{_tag(has_l, "LYP")}'
                f'&nbsp;&nbsp;'
                f'{_tag(has_m, "MAP")}'
                f'&nbsp;&nbsp;'
                f'{_tag(has_x, "XML")}'
            )
        else:
            file_html = (
                '&nbsp;&nbsp;'
                '<font color="#B71C1C" style="font-size:10px">'
                'no files configured — open Technology Config'
                '</font>'
            )

        lyp_name = ""
        if has_l:
            import os
            lyp_name = (
                '&nbsp;&nbsp;'
                f'<font color="#555" style="font-size:9px">'
                f'({os.path.basename(tech_config.get_lyp())})'
                f'</font>'
            )

        _label.setText(
            f'&nbsp;&nbsp;<font color="#333" style="font-size:10px">Profile:</font>'
            f'&nbsp;{profile_html}'
            f'{file_html}'
            f'{lyp_name}'
            f'&nbsp;&nbsp;'
        )
    except Exception:
        pass
